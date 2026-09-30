#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACKAGE = ROOT / "bio2rtl"
RUNNER_VERSION = "bio2rtl-production-runner-generic-v0.3"


def sha256_file(path: Path) -> str: 
    h = hashlib.sha256()
    with path.open("rb") as f: 
        for chunk in iter(lambda: f.read(1 << 20), b""): 
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str: 
    return hashlib.sha256(text.encode()).hexdigest()


def package_sha() -> str: 
    h = hashlib.sha256()
    for p in sorted(PACKAGE.glob("*.py")): 
        h.update(p.name.encode() + b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


PACKAGE_SHA = package_sha()


def json_dump(path: Path, data: object) -> None: 
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text(json.dumps(data, indent = 2, sort_keys = True) + "\n")


def require_pass_text(path: Path) -> None: 
    text = path.read_text()
    if "PASS" not in text or ("RESULT" in text and "FAIL" in text): 
        raise RuntimeError(f"proof/report is not PASS: {path}")


def require_json_result_pass(path: Path, key: str = "result") -> None: 
    d = json.loads(path.read_text())
    if str(d.get(key, "")).upper() != "PASS": 
        raise RuntimeError(f"JSON {key} is not PASS: {path}: {d.get(key)}")


@dataclass
class StageResult: 
    stage_id: str
    fingerprint: str
    directory: Path
    outputs: dict[str, Path]
    cached: bool
    elapsed_sec: float


class ProductionRunner: 
    def __init__(self, *, workdir: Path, timeout: float): 
        self.workdir = workdir.resolve()
        self.timeout = timeout
        self.stages_root = self.workdir / "stages"
        self.stages_root.mkdir(parents = True, exist_ok = True)
        self.manifest_path = self.workdir / "manifest.json"
        self.rows: list[dict] = []
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(ROOT) + (os.pathsep + self.env["PYTHONPATH"] if self.env.get("PYTHONPATH") else "")

    def fingerprint(self, stage_id: str, tool: str, inputs: dict[str, Path], params: dict) -> str: 
        tool_path = HERE / tool if tool.endswith(".py") else None
        payload = {
            "runner_version": RUNNER_VERSION, 
            "stage_id": stage_id, 
            "tool": tool, 
            "tool_sha256": sha256_file(tool_path) if tool_path and tool_path.exists() else None, 
            "package_sha256": PACKAGE_SHA, 
            "inputs": {k: sha256_file(v) for k, v in sorted(inputs.items())}, 
            "params": params, 
        }
        return sha256_text(json.dumps(payload, sort_keys = True, separators = (",", ":")))

    def run_stage(
        self, 
        *, 
        seq: int, 
        stage_id: str, 
        tool: str, 
        inputs: dict[str, Path], 
        output_names: Iterable[str], 
        params: dict, 
        command_builder: Callable[[Path, dict[str, Path]], list[str]], 
        verifier: Callable[[dict[str, Path], Path, str], None] | None = None, 
        timeout: float | None = None, 
    ) -> StageResult: 
        for name, p in inputs.items(): 
            if not p.exists(): 
                raise RuntimeError(f"stage {stage_id}: missing input {name}: {p}")
        fp = self.fingerprint(stage_id, tool, inputs, params)
        sd = self.stages_root / f"{seq:02d}_{stage_id}" / fp[:20]
        sd.mkdir(parents = True, exist_ok = True)
        outputs = {name: sd / name for name in output_names}
        done = sd / "done.json"
        if done.exists(): 
            d = json.loads(done.read_text())
            if d.get("status") == "PASS" and d.get("fingerprint") == fp: 
                good = True
                for name, meta in d.get("outputs", {}).items(): 
                    p = outputs.get(name)
                    if p is None or not p.exists() or sha256_file(p) != meta.get("sha256"): 
                        good = False
                        break
                if good and set(d.get("outputs", {})) == set(outputs): 
                    row = {
                        "seq": seq, "stage": stage_id, "fingerprint": fp, 
                        "cached": True, "elapsed_sec": 0.0, 
                        "outputs": {k: {"path": str(v), "sha256": sha256_file(v)} for k, v in outputs.items()}, 
                    }
                    self.rows.append(row)
                    self._save_manifest()
                    return StageResult(stage_id, fp, sd, outputs, True, 0.0)

        cmd = command_builder(sd, outputs)
        stdout = sd / "stdout.log"
        stderr = sd / "stderr.log"
        started = time.time()
        status = "FAIL"
        failure = ""
        try: 
            cp = subprocess.run(
                cmd, 
                cwd = ROOT, 
                env = self.env, 
                text = True, 
                capture_output = True, 
                timeout = self.timeout if timeout is None else timeout, 
            )
            stdout.write_text(cp.stdout)
            stderr.write_text(cp.stderr)
            if cp.returncode != 0: 
                raise RuntimeError(f"exit={cp.returncode}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
            missing = [str(p) for p in outputs.values() if not p.exists()]
            if missing: 
                raise RuntimeError(f"declared outputs missing: {missing}")
            if verifier: 
                verifier(outputs, sd, cp.stdout)
            status = "PASS"
        except subprocess.TimeoutExpired as e: 
            stdout.write_text(e.stdout or "")
            stderr.write_text(e.stderr or "")
            status = "TIMEOUT"
            failure = f"timeout after {self.timeout if timeout is None else timeout}s"
        except Exception as e: 
            status = "FAIL"
            failure = str(e)
        elapsed = time.time() - started
        record = {
            "runner_version": RUNNER_VERSION, 
            "stage": stage_id, 
            "seq": seq, 
            "fingerprint": fp, 
            "tool": tool, 
            "command": cmd, 
            "package_sha256": PACKAGE_SHA, 
            "inputs": {k: {"path": str(v), "sha256": sha256_file(v)} for k, v in inputs.items()}, 
            "params": params, 
            "status": status, 
            "elapsed_sec": elapsed, 
            "failure": failure, 
            "stdout": str(stdout), 
            "stderr": str(stderr), 
            "outputs": (
                {k: {"path": str(v), "sha256": sha256_file(v)} for k, v in outputs.items()}
                if status == "PASS" else {}
            ), 
        }
        json_dump(done, record)
        self.rows.append(record)
        self._save_manifest()
        if status != "PASS": 
            raise RuntimeError(f"stage {stage_id} {status}: {failure}; see {sd}")
        return StageResult(stage_id, fp, sd, outputs, False, elapsed)

    def _save_manifest(self) -> None: 
        json_dump(self.manifest_path, {
            "version": RUNNER_VERSION, 
            "package_sha256": PACKAGE_SHA, 
            "workdir": str(self.workdir), 
            "stages": self.rows, 
        })


def pytool(name: str, *args: object) -> list[str]: 
    return [sys.executable, str(HERE / name), *map(str, args)]


def pymodule(module: str, *args: object) -> list[str]: 
    return [sys.executable, "-m", module, *map(str, args)]


def main() -> int: 
    ap = argparse.ArgumentParser(description = "Fresh content-addressed .dis -> proof-backed structural bio2rtl production runner")
    ap.add_argument("--dis", type = Path, required = True)
    ap.add_argument("--workdir", type = Path, required = True)
    ap.add_argument("--output", type = Path, required = True)
    ap.add_argument("--timeout", type = float, default = 180.0)
    ap.add_argument("--stop-after", help = "stop after this stage id (for deterministic staged recovery)")
    a = ap.parse_args()
    dis = a.dis.resolve()
    if not dis.exists(): 
        raise SystemExit(f"missing .dis: {dis}")
    r = ProductionRunner(workdir = a.workdir, timeout = a.timeout)

    def stage(**kw): 
        res = r.run_stage(**kw)
        print(f"[{kw['seq']:02d}] {kw['stage_id']}: {'CACHE' if res.cached else 'PASS'} {res.elapsed_sec:.2f}s")
        if a.stop_after == kw["stage_id"]: 
            out = Path(a.output)
            out.parent.mkdir(parents = True, exist_ok = True)
            json_dump(out.with_suffix(out.suffix + ".partial.json"), {
                "version": RUNNER_VERSION, 
                "stopped_after": kw["stage_id"], 
                "stage_dir": str(res.directory), 
                "manifest": str(r.manifest_path), 
            })
            raise SystemExit(0)
        return res.outputs

    # 00: fixture-free semantic frontend. Only reports needed by scheduler recovery are requested.
    o = stage(seq = 0, stage_id = "frontend", tool = "bio2rtl.cli", inputs = {"dis": dis}, 
        output_names = ["semantic.sv", "state.txt", "state.txt.json", "canonical_event.txt", "canonical_event.txt.json", "polling.txt", "polling.txt.json", "fse.txt", "fse.txt.json", "state_role.txt", "state_role.txt.json"], 
        params = {"backend": "semantic-default", "minimal_scheduler_reports": True}, 
        command_builder = lambda sd, out: pymodule("bio2rtl.cli", dis, "-o", out["semantic.sv"], 
            "--state-report", out["state.txt"], "--canonical-event-report", out["canonical_event.txt"], 
            "--polling-phase-event-report", out["polling.txt"], "--feasible-symbolic-executor-report", out["fse.txt"], 
            "--semantic-state-role-report", out["state_role.txt"]), 
        verifier = lambda out, sd, stdout: (_ for _ in ()).throw(RuntimeError("frontend semantic checks not PASS")) if "SYSTEMVERILOG       : PASS" not in stdout else None)
    frontend = o

    o = stage(seq = 1, stage_id = "scheduler_recovery", tool = "recover_scheduler_template.py", 
        inputs = {"fse": frontend["fse.txt.json"], "state": frontend["state.txt.json"], "state_role": frontend["state_role.txt.json"], "canonical_event": frontend["canonical_event.txt.json"], "polling": frontend["polling.txt.json"], "semantic_sv": frontend["semantic.sv"]}, 
        output_names = ["scheduler.json"], params = {"fixture_free": True}, 
        command_builder = lambda sd, out: pytool("recover_scheduler_template.py", "--fse", frontend["fse.txt.json"], "--state", frontend["state.txt.json"], "--state-role", frontend["state_role.txt.json"], "--canonical-event", frontend["canonical_event.txt.json"], "--polling", frontend["polling.txt.json"], "--semantic-sv", frontend["semantic.sv"], "-o", out["scheduler.json"]), 
        verifier = lambda out, sd, stdout: (_ for _ in ()).throw(RuntimeError("scheduler fixture-free check absent")) if "fixture_free=PASS" not in stdout else None)
    scheduler = o

    o = stage(seq = 2, stage_id = "corrected_relation_raw", tool = "materialize_corrected_dedicated_event.py", 
        inputs = {"fse": frontend["fse.txt.json"], "scheduler": scheduler["scheduler.json"]}, output_names = ["corrected_raw.json"], params = {"semantic_promotion": False}, 
        command_builder = lambda sd, out: pytool("materialize_corrected_dedicated_event.py", "--fse", frontend["fse.txt.json"], "--scheduler-template", scheduler["scheduler.json"], "-o", out["corrected_raw.json"]), 
        verifier = lambda out, sd, stdout: (_ for _ in ()).throw(RuntimeError("raw corrected relation structural check not PASS")) if "structural_pass=PASS" not in stdout else None)
    corrected_raw = o

    o = stage(seq = 3, stage_id = "corrected_relation_verify", tool = "promote_corrected_dedicated_event_relation.py", 
        inputs = {"fse": frontend["fse.txt.json"], "raw_ir": corrected_raw["corrected_raw.json"]}, output_names = ["corrected.json", "verify.txt", "verify.txt.json"], params = {"independent_semantic_promotion": True}, 
        command_builder = lambda sd, out: pytool("promote_corrected_dedicated_event_relation.py", "--fse", frontend["fse.txt.json"], "--raw-ir", corrected_raw["corrected_raw.json"], "--output-ir", out["corrected.json"], "--report", out["verify.txt"]), 
        verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]))
    corrected = o

    o = stage(seq = 4, stage_id = "storage48_analysis", tool = "analyze_corrected_abstract_storage.py", 
        inputs = {"fse": frontend["fse.txt.json"], "corrected": corrected["corrected.json"]}, output_names = ["analysis.txt", "analysis.txt.json"], params = {"proof": "FSE_PER_REGISTER_CARTESIAN_OVERAPPROX_V1"}, 
        command_builder = lambda sd, out: pytool("analyze_corrected_abstract_storage.py", "--transitions", frontend["fse.txt.json"], "--dedicated-ir", corrected["corrected.json"], "--output", out["analysis.txt"]), 
        verifier = lambda out, sd, stdout: None)
    storage_analysis = o

    o = stage(seq = 5, stage_id = "storage48_apply", tool = "annotate_corrected_abstract_storage.py", 
        inputs = {"corrected": corrected["corrected.json"], "analysis": storage_analysis["analysis.txt.json"]}, output_names = ["storage48.json"], params = {}, 
        command_builder = lambda sd, out: pytool("annotate_corrected_abstract_storage.py", "--dedicated-ir", corrected["corrected.json"], "--analysis", storage_analysis["analysis.txt.json"], "--output", out["storage48.json"]), verifier = None)
    storage48 = o

    o = stage(seq = 6, stage_id = "storage48_verify", tool = "verify_corrected_abstract_storage.py", 
        inputs = {"corrected": corrected["corrected.json"], "storage48": storage48["storage48.json"]}, output_names = ["verify.txt"], params = {"fixture_storage_counts": False}, 
        command_builder = lambda sd, out: pytool("verify_corrected_abstract_storage.py", "--source-ir", corrected["corrected.json"], "--storage-ir", storage48["storage48.json"], "--output", out["verify.txt"]), 
        verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]))

    o = stage(seq = 7, stage_id = "exprmin48", tool = "minimize_dedicated_event_expressions.py", 
        inputs = {"storage48": storage48["storage48.json"]}, output_names = ["exprmin48.json", "report.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("minimize_dedicated_event_expressions.py", "--ir", storage48["storage48.json"], "--output", out["exprmin48.json"], "--report", out["report.txt"]), verifier = None)
    exprmin48 = o

    o = stage(seq = 8, stage_id = "exprmin48_verify", tool = "verify_dedicated_event_expression_minimization.py", 
        inputs = {"source": storage48["storage48.json"], "optimized": exprmin48["exprmin48.json"]}, output_names = ["verify.txt", "verify.txt.json"], params = {}, 
        command_builder = lambda sd, out: pytool("verify_dedicated_event_expression_minimization.py", "--source-ir", storage48["storage48.json"], "--optimized-ir", exprmin48["exprmin48.json"], "--output", out["verify.txt"]), 
        verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]))

    o = stage(seq = 9, stage_id = "guardmin48", tool = "minimize_corrected_dedicated_event_guards.py", 
        inputs = {"storage48": storage48["storage48.json"]}, output_names = ["guardmin48.json"], params = {"strict": True, "branch_from_complete_storage": True}, 
        command_builder = lambda sd, out: pytool("minimize_corrected_dedicated_event_guards.py", "--ir", storage48["storage48.json"], "--output", out["guardmin48.json"]), verifier = None)
    guardmin = o

    o = stage(seq = 10, stage_id = "guardmin48_verify", tool = "verify_corrected_guard_minimization.py", 
        inputs = {"source": storage48["storage48.json"], "minimized": guardmin["guardmin48.json"]}, output_names = ["verify.txt", "verify.txt.json"], params = {"branch_from_complete_storage": True}, 
        command_builder = lambda sd, out: pytool("verify_corrected_guard_minimization.py", "--source-ir", storage48["storage48.json"], "--minimized-ir", guardmin["guardmin48.json"], "--output", out["verify.txt"]), 
        verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]))

    o = stage(seq = 11, stage_id = "derived47_analysis", tool = "analyze_dedicated_event_derived_state_parallel.py", 
        inputs = {"complete": storage48["storage48.json"], "baseline": guardmin["guardmin48.json"]}, output_names = ["analysis.txt", "analysis.json"], params = {"generic_pairwise": True, "parallel_workers": 12, "proof_equivalent_to_sequential": True}, 
        command_builder = lambda sd, out: pytool("analyze_dedicated_event_derived_state_parallel.py", "--complete-ir", storage48["storage48.json"], "--baseline-ir", guardmin["guardmin48.json"], "--output", out["analysis.txt"], "--json-output", out["analysis.json"], "--jobs", "12"), verifier = None, 
        timeout = max(a.timeout, 120.0))
    derived_analysis = o

    o = stage(seq = 12, stage_id = "derived47_apply", tool = "build_derived47_candidate.py", 
        inputs = {"complete": storage48["storage48.json"], "baseline": guardmin["guardmin48.json"], "analysis": derived_analysis["analysis.json"]}, output_names = ["derived47.json", "report.txt"], params = {"cached_analysis": True}, 
        command_builder = lambda sd, out: pytool("build_derived47_candidate.py", "--complete-ir", storage48["storage48.json"], "--baseline-ir", guardmin["guardmin48.json"], "--analysis-json", derived_analysis["analysis.json"], "--output", out["derived47.json"], "--report", out["report.txt"]), verifier = None)
    derived47 = o

    o = stage(seq = 13, stage_id = "derived47_verify", tool = "verify_derived47_candidate.py", 
        inputs = {"complete": storage48["storage48.json"], "baseline": guardmin["guardmin48.json"], "candidate": derived47["derived47.json"]}, output_names = ["verify.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("verify_derived47_candidate.py", "--complete-ir", storage48["storage48.json"], "--baseline-ir", guardmin["guardmin48.json"], "--candidate-ir", derived47["derived47.json"], "--output", out["verify.txt"]), 
        verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]))

    o = stage(seq = 14, stage_id = "packed44", tool = "build_packedmask_storage_candidate.py", 
        inputs = {"derived47": derived47["derived47.json"]}, output_names = ["packed44.json", "report.txt"], params = {"semantic_recoding": False}, 
        command_builder = lambda sd, out: pytool("build_packedmask_storage_candidate.py", "--source-ir", derived47["derived47.json"], "--output-ir", out["packed44.json"], "--report", out["report.txt"]), verifier = None)
    packed44 = o

    o = stage(seq = 15, stage_id = "complete44", tool = "materialize_complete_with_storage_plan.py", 
        inputs = {"complete48": storage48["storage48.json"], "plan44": packed44["packed44.json"]}, output_names = ["complete44.json", "report.txt"], params = {"preserve_complete_relation": True}, 
        command_builder = lambda sd, out: pytool("materialize_complete_with_storage_plan.py", "--complete-ir", storage48["storage48.json"], "--storage-plan-ir", packed44["packed44.json"], "--output", out["complete44.json"], "--report", out["report.txt"]), verifier = None)
    complete44 = o

    o = stage(seq = 16, stage_id = "joint_domain44", tool = "analyze_joint_control_domain.py", 
        inputs = {"complete44": complete44["complete44.json"], "baseline44": packed44["packed44.json"]}, output_names = ["domain.txt", "domain.json"], params = {"order": "before-expression-minimization"}, 
        command_builder = lambda sd, out: pytool("analyze_joint_control_domain.py", "--complete-ir", complete44["complete44.json"], "--baseline-ir", packed44["packed44.json"], "--output", out["domain.txt"], "--json-output", out["domain.json"]), verifier = None, 
        timeout = max(a.timeout, 240.0))
    joint_domain = o

    o = stage(seq = 17, stage_id = "exprmin44", tool = "minimize_dedicated_event_expressions.py", 
        inputs = {"complete44": complete44["complete44.json"]}, output_names = ["exprmin44.json", "report.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("minimize_dedicated_event_expressions.py", "--ir", complete44["complete44.json"], "--output", out["exprmin44.json"], "--report", out["report.txt"]), verifier = None)
    exprmin44 = o

    o = stage(seq = 18, stage_id = "exprmin44_verify", tool = "verify_dedicated_event_expression_minimization.py", 
        inputs = {"source": complete44["complete44.json"], "optimized": exprmin44["exprmin44.json"]}, output_names = ["verify.txt", "verify.txt.json"], params = {}, 
        command_builder = lambda sd, out: pytool("verify_dedicated_event_expression_minimization.py", "--source-ir", complete44["complete44.json"], "--optimized-ir", exprmin44["exprmin44.json"], "--output", out["verify.txt"]), verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]))

    o = stage(seq = 19, stage_id = "joint44", tool = "build_jointctrl_cached.py", 
        inputs = {"exprmin44": exprmin44["exprmin44.json"], "domain": joint_domain["domain.json"]}, output_names = ["joint44.json", "report.txt"], params = {"cached_domain": True}, 
        command_builder = lambda sd, out: pytool("build_jointctrl_cached.py", "--expression-minimized-complete-ir", exprmin44["exprmin44.json"], "--joint-domain", joint_domain["domain.json"], "--output", out["joint44.json"], "--report", out["report.txt"]), verifier = None)
    joint44 = o

    o = stage(seq = 20, stage_id = "joint44_verify", tool = "verify_jointctrl_cached.py", 
        inputs = {"exprmin44": exprmin44["exprmin44.json"], "domain": joint_domain["domain.json"], "joint44": joint44["joint44.json"]}, output_names = ["verify.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("verify_jointctrl_cached.py", "--expression-minimized-complete-ir", exprmin44["exprmin44.json"], "--joint-domain", joint_domain["domain.json"], "--candidate-ir", joint44["joint44.json"], "--output", out["verify.txt"]), verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]))

    o = stage(seq = 21, stage_id = "joint44_emit", tool = "emit_dedicated_event_storage_opt_sv.py", 
        inputs = {"joint44": joint44["joint44.json"]}, output_names = ["joint44.sv"], params = {"precise": True, "bitwise_update": True, "bitwise_outcome": True}, 
        command_builder = lambda sd, out: pytool("emit_dedicated_event_storage_opt_sv.py", "--ir", joint44["joint44.json"], "--output", out["joint44.sv"], "--precise-expressions", "--bitwise-update-equations", "--bitwise-outcome-lowering"), verifier = None)
    joint44_sv = o

    # Quotient analysis is defined over the complete relation. joint44 is a sparse
    # storage-optimized IR, whereas exprmin44 retains the complete transition rows.
    o = stage(seq = 22, stage_id = "quotient44", tool = "analyze_behavioral_control_quotient.py", 
        inputs = {"complete44": exprmin44["exprmin44.json"], "domain": joint_domain["domain.json"]}, output_names = ["report.txt", "quotient.json"], params = {"relation": "complete44"}, 
        command_builder = lambda sd, out: pytool("analyze_behavioral_control_quotient.py", "--ir", exprmin44["exprmin44.json"], "--domain", joint_domain["domain.json"], "--output", out["report.txt"], "--json-output", out["quotient.json"]), verifier = None)
    quotient = o

    o = stage(seq = 23, stage_id = "direct_table44", tool = "build_behavioral_direct_table.py", 
        inputs = {"complete": exprmin44["exprmin44.json"], "storage": joint44["joint44.json"], "domain": joint_domain["domain.json"], "quotient": quotient["quotient.json"]}, output_names = ["direct_table.json", "report.txt"], params = {"rtl_emitted": False}, 
        command_builder = lambda sd, out: pytool("build_behavioral_direct_table.py", "--complete-ir", exprmin44["exprmin44.json"], "--storage-ir", joint44["joint44.json"], "--domain", joint_domain["domain.json"], "--quotient", quotient["quotient.json"], "--output", out["direct_table.json"], "--report", out["report.txt"]), verifier = lambda out, sd, stdout: require_pass_text(out["report.txt"]))
    direct_table = o

    o = stage(seq = 24, stage_id = "legal_phase_product", tool = "build_legal_qualified_edge_product.py", 
        inputs = {"ir": joint44["joint44.json"], "direct_table": direct_table["direct_table.json"]}, output_names = ["legal_phase.json", "report.txt"], params = {"event_names_hardcoded": False}, 
        command_builder = lambda sd, out: pytool("build_legal_qualified_edge_product.py", "--ir", joint44["joint44.json"], "--direct-table", direct_table["direct_table.json"], "--output", out["legal_phase.json"], "--report", out["report.txt"]), verifier = lambda out, sd, stdout: require_json_result_pass(out["legal_phase.json"], "proof_result"))
    legal_phase = o

    o = stage(seq = 25, stage_id = "phase40_analysis", tool = "analyze_phase_local_state_elision_v2.py", 
        inputs = {"ir": joint44["joint44.json"], "quotient": quotient["quotient.json"], "legal_phase": legal_phase["legal_phase.json"]}, output_names = ["analysis.json"], params = {"external_legal_product": True}, 
        command_builder = lambda sd, out: pytool("analyze_phase_local_state_elision_v2.py", "--ir", joint44["joint44.json"], "--quotient", quotient["quotient.json"], "--legal-product", legal_phase["legal_phase.json"], "--output", out["analysis.json"]), verifier = None)
    phase_analysis = o

    o = stage(seq = 26, stage_id = "phase40_apply", tool = "build_phase_local_state_elided_sv.py", 
        inputs = {"source_sv": joint44_sv["joint44.sv"], "storage_ir": joint44["joint44.json"], "analysis": phase_analysis["analysis.json"]}, output_names = ["phase40.sv", "phase40.ir.json", "meta.json", "report.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("build_phase_local_state_elided_sv.py", "--source-sv", joint44_sv["joint44.sv"], "--storage-ir", joint44["joint44.json"], "--analysis", phase_analysis["analysis.json"], "--output-sv", out["phase40.sv"], "--output-ir", out["phase40.ir.json"], "--metadata", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    phase40 = o

    o = stage(seq = 27, stage_id = "phase40_dce", tool = "dce_generated_decode_sv.py", 
        inputs = {"source": phase40["phase40.sv"]}, output_names = ["cleanphase40.sv", "meta.json", "report.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("dce_generated_decode_sv.py", "--source", phase40["phase40.sv"], "--output", out["cleanphase40.sv"], "--meta", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    clean40 = o

    o = stage(seq = 28, stage_id = "corr39_analysis", tool = "analyze_correlated_state_groups.py", 
        inputs = {"storage_ir": phase40["phase40.ir.json"], "domain": joint_domain["domain.json"]}, output_names = ["analysis.json", "report.txt"], params = {"min_group_size": 3, "min_savings": 1}, 
        command_builder = lambda sd, out: pytool("analyze_correlated_state_groups.py", "--storage-ir", phase40["phase40.ir.json"], "--domain", joint_domain["domain.json"], "--output-json", out["analysis.json"], "--report", out["report.txt"], "--min-group-size", "3", "--min-savings", "1"), verifier = None)
    corr_analysis = o

    o = stage(seq = 29, stage_id = "corr39_apply", tool = "build_correlated_group_encoded_sv.py", 
        inputs = {"source_sv": clean40["cleanphase40.sv"], "storage_ir": phase40["phase40.ir.json"], "analysis": corr_analysis["analysis.json"]}, output_names = ["corr39.sv", "meta.json", "report.txt"], params = {"recommendation": "max_savings"}, 
        command_builder = lambda sd, out: pytool("build_correlated_group_encoded_sv.py", "--source-sv", clean40["cleanphase40.sv"], "--storage-ir", phase40["phase40.ir.json"], "--analysis", corr_analysis["analysis.json"], "--recommendation", "max_savings", "--output-sv", out["corr39.sv"], "--metadata", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    corr39 = o

    o = stage(seq = 30, stage_id = "corr39_verify", tool = "verify_correlated_group_candidate.py", 
        inputs = {"complete_ir": joint44["joint44.json"], "domain": joint_domain["domain.json"], "meta": corr39["meta.json"]}, output_names = ["verify.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("verify_correlated_group_candidate.py", "--complete-ir", joint44["joint44.json"], "--domain", joint_domain["domain.json"], "--metadata", corr39["meta.json"], "--output", out["verify.txt"]), verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]))

    o = stage(seq = 31, stage_id = "hist37_analysis", tool = "analyze_history_coalescing.py", 
        inputs = {"ir": joint44["joint44.json"], "domain": joint_domain["domain.json"]}, output_names = ["analysis.json", "report.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("analyze_history_coalescing.py", "--ir", joint44["joint44.json"], "--domain", joint_domain["domain.json"], "--output-json", out["analysis.json"], "--report", out["report.txt"]), verifier = None)
    hist_analysis = o

    o = stage(seq = 32, stage_id = "hist37_apply", tool = "build_history_coalesced_sv.py", 
        inputs = {"source_sv": corr39["corr39.sv"], "ir": joint44["joint44.json"], "analysis": hist_analysis["analysis.json"]}, output_names = ["hist37.sv", "meta.json", "report.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("build_history_coalesced_sv.py", "--source-sv", corr39["corr39.sv"], "--ir", joint44["joint44.json"], "--analysis", hist_analysis["analysis.json"], "--output-sv", out["hist37.sv"], "--metadata", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    hist37 = o

    o = stage(seq = 33, stage_id = "hist37_verify_sv", tool = "verify_history_coalesced_sv.py", 
        inputs = {"sv": hist37["hist37.sv"], "meta": hist37["meta.json"]}, output_names = ["verify.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("verify_history_coalesced_sv.py", "--sv", hist37["hist37.sv"], "--metadata", hist37["meta.json"], "--output", out["verify.txt"]), verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]))

    o = stage(seq = 34, stage_id = "hist37_verify_model", tool = "verify_history_coalescing_model.py", 
        inputs = {"ir": joint44["joint44.json"], "meta": hist37["meta.json"]}, output_names = ["verify.txt"], params = {"cycles": 4096, "seed": "0xC011EA5E"}, 
        command_builder = lambda sd, out: pytool("verify_history_coalescing_model.py", "--ir", joint44["joint44.json"], "--metadata", hist37["meta.json"], "--cycles", "4096", "--seed", "0xC011EA5E", "--trace-dir", sd/"traces", "--output", out["verify.txt"]), verifier = lambda out, sd, stdout: require_pass_text(out["verify.txt"]), timeout = max(a.timeout, 240.0))

    o = stage(seq = 35, stage_id = "protocol_counter_proof", tool = "analyze_protocol_counter_ownership_generic.py", 
        inputs = {"direct_table": direct_table["direct_table.json"], "quotient": quotient["quotient.json"], "ir": joint44["joint44.json"], "legal_phase": legal_phase["legal_phase.json"]}, output_names = ["proof.json", "edges.json", "report.txt"], params = {"generic_pair_discovery": True, "generic_policy_search": True}, 
        command_builder = lambda sd, out: pytool("analyze_protocol_counter_ownership_generic.py", "--direct-table", direct_table["direct_table.json"], "--quotient", quotient["quotient.json"], "--ir", joint44["joint44.json"], "--legal-phase-product", legal_phase["legal_phase.json"], "--output", out["proof.json"], "--report", out["report.txt"], "--edge-cache", out["edges.json"]), verifier = lambda out, sd, stdout: require_json_result_pass(out["proof.json"]), timeout = max(a.timeout, 300.0))
    counter = o

    o = stage(seq = 36, stage_id = "protocol33_apply", tool = "build_protocol_counter_semantic_bridge_sv_v3.py", 
        inputs = {"source_sv": hist37["hist37.sv"], "ir": joint44["joint44.json"], "proof": counter["proof.json"]}, output_names = ["protocol33.sv", "meta.json", "report.txt"], params = {"proof_driven": True}, 
        command_builder = lambda sd, out: pytool("build_protocol_counter_semantic_bridge_sv_v3.py", "--source-sv", hist37["hist37.sv"], "--ir", joint44["joint44.json"], "--proof", counter["proof.json"], "--output-sv", out["protocol33.sv"], "--metadata", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    protocol33 = o

    o = stage(seq = 37, stage_id = "bitlive32_analysis", tool = "analyze_register_bit_liveness.py", 
        inputs = {"ir": joint44["joint44.json"]}, output_names = ["analysis.json"], params = {}, 
        command_builder = lambda sd, out: pytool("analyze_register_bit_liveness.py", "--ir", joint44["joint44.json"], "--output", out["analysis.json"]), verifier = None)
    bitlive = o

    o = stage(seq = 38, stage_id = "bitlive32_apply", tool = "build_dead_high_bit_elided_sv.py", 
        inputs = {"source_sv": protocol33["protocol33.sv"], "analysis": bitlive["analysis.json"]}, output_names = ["bitlive32.sv", "meta.json", "report.txt"], params = {"source_storage_bits": 33}, 
        command_builder = lambda sd, out: pytool("build_dead_high_bit_elided_sv.py", "--source-sv", protocol33["protocol33.sv"], "--analysis", bitlive["analysis.json"], "--source-storage-bits", "33", "--output-sv", out["bitlive32.sv"], "--metadata", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    bitlive32 = o

    o = stage(seq = 39, stage_id = "obslocal31_analysis", tool = "analyze_observation_local_state_specialization.py", 
        inputs = {"ir": joint44["joint44.json"], "quotient": quotient["quotient.json"], "legal_product": counter["edges.json"]}, output_names = ["analysis.json", "report.txt"], params = {"schema_driven": True}, 
        command_builder = lambda sd, out: pytool("analyze_observation_local_state_specialization.py", "--ir", joint44["joint44.json"], "--quotient", quotient["quotient.json"], "--legal-product", counter["edges.json"], "--output", out["analysis.json"], "--report", out["report.txt"]), verifier = None)
    obs31a = o

    o = stage(seq = 40, stage_id = "obslocal31_apply", tool = "build_observation_local_state_specialized_sv.py", 
        inputs = {"source_sv": bitlive32["bitlive32.sv"], "ir": joint44["joint44.json"], "analysis": obs31a["analysis.json"]}, output_names = ["obslocal31.sv", "meta.json", "report.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("build_observation_local_state_specialized_sv.py", "--source-sv", bitlive32["bitlive32.sv"], "--ir", joint44["joint44.json"], "--analysis", obs31a["analysis.json"], "--output-sv", out["obslocal31.sv"], "--metadata", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    obs31 = o

    o = stage(seq = 41, stage_id = "obslocal30_analysis", tool = "analyze_observation_local_packed_bit_specialization_v2.py", 
        inputs = {"ir": joint44["joint44.json"], "quotient": quotient["quotient.json"], "direct_table": direct_table["direct_table.json"], "legal_product": counter["edges.json"], "phase_product": legal_phase["legal_phase.json"]}, output_names = ["analysis.json", "report.txt"], params = {"schema_driven": True}, 
        command_builder = lambda sd, out: pytool("analyze_observation_local_packed_bit_specialization_v2.py", "--ir", joint44["joint44.json"], "--quotient", quotient["quotient.json"], "--direct-table", direct_table["direct_table.json"], "--legal-product", counter["edges.json"], "--phase-product", legal_phase["legal_phase.json"], "--output", out["analysis.json"], "--report", out["report.txt"]), verifier = None, timeout = max(a.timeout, 240.0))
    obs30a = o

    o = stage(seq = 42, stage_id = "obslocal30_apply", tool = "build_observation_local_packed_bit_specialized_sv.py", 
        inputs = {"source_sv": obs31["obslocal31.sv"], "ir": joint44["joint44.json"], "analysis": obs30a["analysis.json"]}, output_names = ["obslocal30.sv", "meta.json", "report.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("build_observation_local_packed_bit_specialized_sv.py", "--source-sv", obs31["obslocal31.sv"], "--ir", joint44["joint44.json"], "--analysis", obs30a["analysis.json"], "--output-sv", out["obslocal30.sv"], "--metadata", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    obs30 = o

    o = stage(seq = 43, stage_id = "recurrence30", tool = "factor_shift_clear_recurrence_sv.py", 
        inputs = {"source": obs30["obslocal30.sv"]}, output_names = ["recurrence30.sv", "meta.json", "report.txt"], params = {"generic_shift_clear": True}, 
        command_builder = lambda sd, out: pytool("factor_shift_clear_recurrence_sv.py", "--source", obs30["obslocal30.sv"], "--output", out["recurrence30.sv"], "--meta", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    recurrence30 = o

    o = stage(seq = 44, stage_id = "projection25_analysis", tool = "analyze_observation_storage_projection_v2.py", 
        inputs = {"direct_table": direct_table["direct_table.json"], "ir": joint44["joint44.json"], "phase_product": legal_phase["legal_phase.json"], "counter_proof": counter["proof.json"]}, output_names = ["analysis.json", "report.txt"], params = {"recovered_topology": True}, 
        command_builder = lambda sd, out: pytool("analyze_observation_storage_projection_v2.py", "--direct-table", direct_table["direct_table.json"], "--ir", joint44["joint44.json"], "--phase-product", legal_phase["legal_phase.json"], "--counter-proof", counter["proof.json"], "--output", out["analysis.json"], "--report", out["report.txt"]), verifier = None)
    projection_a = o

    o = stage(seq = 45, stage_id = "projection25_apply", tool = "build_observation_storage_projected_sv.py", 
        inputs = {"source": recurrence30["recurrence30.sv"], "analysis": projection_a["analysis.json"]}, output_names = ["projection25.sv", "meta.json", "report.txt"], params = {}, 
        command_builder = lambda sd, out: pytool("build_observation_storage_projected_sv.py", "--source", recurrence30["recurrence30.sv"], "--analysis", projection_a["analysis.json"], "--output", out["projection25.sv"], "--meta", out["meta.json"], "--report", out["report.txt"]), verifier = None)
    projection25 = o

    o = stage(seq = 46, stage_id = "structural_raw", tool = "map_tr1um_structural_generic.py", 
        inputs = {"behavioral": projection25["projection25.sv"]}, output_names = ["structural.v", "meta.json", "report.txt"], params = {"binary_specific_state_fixture": False, "binary_specific_output_fixture": False}, 
        command_builder = lambda sd, out: pytool("map_tr1um_structural_generic.py", "--source", projection25["projection25.sv"], "--output", out["structural.v"], "--meta", out["meta.json"], "--report", out["report.txt"]), verifier = None, timeout = max(a.timeout, 300.0))
    structural = o

    o = stage(seq = 47, stage_id = "structural_strict", tool = "verify_structural_strict.py", 
        inputs = {"behavioral": projection25["projection25.sv"], "candidate": structural["structural.v"]}, output_names = ["strict.json", "strict.txt"], params = {"check_cycle": True, "check_outputs": True, "check_async": True, "check_drivers": True}, 
        command_builder = lambda sd, out: pytool("verify_structural_strict.py", "--behavioral", projection25["projection25.sv"], "--candidate", structural["structural.v"], "--json", out["strict.json"], "--txt", out["strict.txt"]), verifier = lambda out, sd, stdout: require_json_result_pass(out["strict.json"]), timeout = max(a.timeout, 300.0))
    strict = o

    final = Path(a.output).resolve()
    final.parent.mkdir(parents = True, exist_ok = True)
    shutil.copyfile(structural["structural.v"], final)
    report = {
        "version": RUNNER_VERSION, 
        "input_dis": str(dis), 
        "input_dis_sha256": sha256_file(dis), 
        "output_structural": str(final), 
        "output_sha256": sha256_file(final), 
        "behavioral25": str(projection25["projection25.sv"]), 
        "behavioral25_sha256": sha256_file(projection25["projection25.sv"]), 
        "strict": json.loads(strict["strict.json"].read_text()), 
        "manifest": str(r.manifest_path), 
        "result": "PASS", 
        "note": "This runner currently stops at the strict raw structural stage. The fixed-point area backend is the next production integration stage.", 
    }
    json_dump(final.with_suffix(final.suffix + ".production.json"), report)
    print(f"output={final}")
    print(f"output_sha256={report['output_sha256']}")
    print(f"behavioral25_sha256={report['behavioral25_sha256']}")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__": 
    raise SystemExit(main())
