# bio2rtl repository file guide

This guide lists the role of **all 295 files** retained in the public repository.

For normal use, start with `README.md`, `docs/SETUP.md`, `docs/TOML.md`, `examples/`, `tools/setup_bio2rtl_env.sh`, and `tools/run_bio_sim_example.py`.

`bio2rtl/` is the public compiler/mapping/export package. `semantic_frontend/bio2rtl/` is an isolated low-level semantic engine executed in a separate subprocess/Python namespace; normal users do not call it directly. The repository was reduced from 635 files to 294 by removing historical exploration scripts, audits, release evidence, and legacy mappers that are not on the current production path.

## GitHub / CI
| File | Role |
|---|---|
| `.github/workflows/smoke.yml` | Repository/build file: smoke.yml. |

## Repository root / production entry points
| File | Role |
|---|---|
| `.gitignore` | Repository/build file: .gitignore. |
| `CHANGELOG.md` | Repository/build file: CHANGELOG.md. |
| `FILE_GUIDE.md` | English guide to every file in this repository. |
| `FILE_GUIDE_ja.md` | Japanese guide to every file in this repository. |
| `LICENSE` | Repository/build file: LICENSE. |
| `Makefile` | Repository/build file: Makefile. |
| `README.md` | Default English README: setup, TOML, build flow, support circuits, scope and positioning. |
| `README_ja.md` | Japanese README. |
| `RELEASE_NOTES.md` | Repository/build file: RELEASE_NOTES.md. |
| `generate_architecture_certificates.py` | Repository/build file: generate_architecture_certificates.py. |
| `generate_stage6_core_certificates.py` | Repository/build file: generate_stage6_core_certificates.py. |
| `generate_stage7_dynamic_certificates.py` | Repository/build file: generate_stage7_dynamic_certificates.py. |
| `generate_storage_relations_certificate.py` | Repository/build file: generate_storage_relations_certificate.py. |
| `pyproject.toml` | Repository/build file: pyproject.toml. |
| `recover_seedless_architecture.py` | Repository/build file: recover_seedless_architecture.py. |
| `requirements.txt` | Repository/build file: requirements.txt. |
| `run_compiler.py` | Production orchestrator: semantic frontend, architecture recovery, TR-1um mapping and schematic generation. |
| `run_semantic_frontend.py` | Runs the isolated low-level semantic frontend and publishes canonical semantic artifacts. |
| `verify_generic_core.py` | Repository/build file: verify_generic_core.py. |
| `verify_generic_fullchip.py` | Repository/build file: verify_generic_fullchip.py. |

## Public compiler package (`bio2rtl/`)
| File | Role |
|---|---|
| `bio2rtl/__init__.py` | Root compiler module for   init  . See the Japanese guide for the detailed role. |
| `bio2rtl/__main__.py` | Root compiler module for   main  . See the Japanese guide for the detailed role. |
| `bio2rtl/boolean_contract.py` | Root compiler module for boolean contract. See the Japanese guide for the detailed role. |
| `bio2rtl/boolean_mapper.py` | Root compiler module for boolean mapper. See the Japanese guide for the detailed role. |
| `bio2rtl/cli.py` | Public `bio2rtl init/check/build/benchmark` CLI and isolated-workspace orchestration. |
| `bio2rtl/generic_fullchip_export.py` | Binds POR and electrical I/O support recipes and emits the full-chip hierarchy; fallback core views reference the real `TR-1um_5_stdcell` library. |
| `bio2rtl/global_semantic_mapper.py` | Root compiler module for global semantic mapper. See the Japanese guide for the detailed role. |
| `bio2rtl/semantic_liveness.py` | Computes the live combinational dependency cone and prunes physically unobservable semantic roles before mapping. |
| `bio2rtl/global_semantic_proof.py` | Root compiler module for global semantic proof. See the Japanese guide for the detailed role. |
| `bio2rtl/legal_product_schema.py` | Root compiler module for legal product schema. See the Japanese guide for the detailed role. |
| `bio2rtl/legal_role_synthesis.py` | Root compiler module for legal role synthesis. See the Japanese guide for the detailed role. |
| `bio2rtl/load_hold_discovery.py` | Root compiler module for load hold discovery. See the Japanese guide for the detailed role. |
| `bio2rtl/routed_xschem_export.py` | Creates the canonical routing-aware Xschem core view. |
| `bio2rtl/natural_storage_contracts.py` | Root compiler module for natural storage contracts. See the Japanese guide for the detailed role. |
| `bio2rtl/neutral_bindings.py` | Root compiler module for neutral bindings. See the Japanese guide for the detailed role. |
| `bio2rtl/neutral_graph.py` | Root compiler module for neutral graph. See the Japanese guide for the detailed role. |
| `bio2rtl/no_cert_phase40_mapper.py` | Root compiler module for no cert phase40 mapper. See the Japanese guide for the detailed role. |
| `bio2rtl/observation_projection.py` | Root compiler module for observation projection. See the Japanese guide for the detailed role. |
| `bio2rtl/observation_tx_discovery.py` | Root compiler module for observation tx discovery. See the Japanese guide for the detailed role. |
| `bio2rtl/pass_manager.py` | Root compiler module for pass manager. See the Japanese guide for the detailed role. |
| `bio2rtl/phase40_direct_lowering.py` | Root compiler module for phase40 direct lowering. See the Japanese guide for the detailed role. |
| `bio2rtl/physical_interface.py` | Root compiler module for physical interface. See the Japanese guide for the detailed role. |
| `bio2rtl/physical_ir.py` | Root compiler module for physical ir. See the Japanese guide for the detailed role. |
| `bio2rtl/post_architecture_maturation.py` | Root compiler module for post architecture maturation. See the Japanese guide for the detailed role. |
| `bio2rtl/predicate_projection.py` | Root compiler module for predicate projection. See the Japanese guide for the detailed role. |
| `bio2rtl/project_inputs.py` | Root compiler module for project inputs. See the Japanese guide for the detailed role. |
| `bio2rtl/proof_bitset.py` | Root compiler module for proof bitset. See the Japanese guide for the detailed role. |
| `bio2rtl/recipe_keys.py` | Root compiler module for recipe keys. See the Japanese guide for the detailed role. |
| `bio2rtl/recover_seedless.py` | Root compiler module for recover seedless. See the Japanese guide for the detailed role. |
| `bio2rtl/semantic_component_manifest.py` | Root compiler module for semantic component manifest. See the Japanese guide for the detailed role. |
| `bio2rtl/semantic_contracts.py` | Root compiler module for semantic contracts. See the Japanese guide for the detailed role. |
| `bio2rtl/semantic_projection.py` | Root compiler module for semantic projection. See the Japanese guide for the detailed role. |
| `bio2rtl/stage6_generic_certificates.py` | Root compiler module for stage6 generic certificates. See the Japanese guide for the detailed role. |
| `bio2rtl/tr1um_declarative_mapper.py` | Current production mapper from Dedicated Hardware IR to TR-1um standard cells. |
| `bio2rtl/truth_minimize.py` | Root compiler module for truth minimize. See the Japanese guide for the detailed role. |
| `bio2rtl/xschem_export.py` | Emits core SPICE and intermediate Xschem data; temporary generated cell symbols are not retained in final user-facing output. |

## Documentation (`docs/`)
| File | Role |
|---|---|
| `docs/CLI.md` | Documentation: CLI. |
| `docs/I2C_ELECTRICAL_TEST.md` | Documentation: I2C ELECTRICAL TEST. |
| `docs/SETUP.md` | Documentation: SETUP. |
| `docs/TOML.md` | Documentation: TOML. |
| `docs/VERIFICATION.md` | Documentation: VERIFICATION. |
| `docs/XSCHEM_OUTPUTS.md` | Documentation: XSCHEM OUTPUTS. |

## Examples and references (`examples/`)
| File | Role |
|---|---|
| `examples/BIO_SIM_SOURCES.md` | Example asset for BIO_SIM_SOURCES.md: BIO_SIM_SOURCES.md. |
| `examples/set_after_3_rises/README.md` | Example asset for set_after_3_rises: README.md. |
| `examples/set_after_3_rises/bio2rtl.toml` | Example asset for set_after_3_rises: bio2rtl.toml. |
| `examples/set_after_3_rises/bio_sim/main.c` | Example asset for set_after_3_rises: main.c. |
| `examples/set_after_3_rises/set_after_3_rises.dis` | Example asset for set_after_3_rises: set_after_3_rises.dis. |
| `examples/i2c_gpio_2bit/README.md` | Example asset for i2c_gpio_2bit: README.md. |
| `examples/i2c_gpio_2bit/bio2rtl.toml` | Example asset for i2c_gpio_2bit: bio2rtl.toml. |
| `examples/i2c_gpio_2bit/bio_sim/main.c` | Example asset for i2c_gpio_2bit: main.c. |
| `examples/i2c_gpio_2bit/i2c-gpio-hw.dis` | Example asset for i2c_gpio_2bit: i2c-gpio-hw.dis. |
| `examples/i2c_gpio_2bit/reference/i2c_electrical_benchmark_expected.json` | Example asset for i2c_gpio_2bit: i2c_electrical_benchmark_expected.json. |
| `examples/i2c_gpio_2bit/reference/i2c_gpio_2bit_fullchip.sym` | Example asset for i2c_gpio_2bit: i2c_gpio_2bit_fullchip.sym. |
| `examples/i2c_gpio_2bit/reference/i2c_gpio_2bit_i2c_benchmark.sch` | Example asset for i2c_gpio_2bit: i2c_gpio_2bit_i2c_benchmark.sch. |
| `examples/i2c_gpio_2bit/reference/manual_layout_122/i2c_gpio_core.sch` | Example asset for i2c_gpio_2bit: i2c_gpio_core.sch. |
| `examples/i2c_gpio_2bit/reference/manual_layout_122/i2c_gpio_core.sym` | Example asset for i2c_gpio_2bit: i2c_gpio_core.sym. |
| `examples/i2c_gpio_2bit/reference/manual_layout_122/i2c_gpio_fullchip.sch` | Example asset for i2c_gpio_2bit: i2c_gpio_fullchip.sch. |
| `examples/clock_divider_by4/README.md` | Example asset for clock_divider_by4: README.md. |
| `examples/clock_divider_by4/bio2rtl.toml` | Example asset for clock_divider_by4: bio2rtl.toml. |
| `examples/clock_divider_by4/bio_sim/main.c` | Example asset for clock_divider_by4: main.c. |
| `examples/clock_divider_by4/clock_divider_by4.dis` | Example asset for clock_divider_by4: clock_divider_by4.dis. |
| `examples/serial_pattern_1101/README.md` | Example asset for serial_pattern_1101: README.md. |
| `examples/serial_pattern_1101/bio2rtl.toml` | Example asset for serial_pattern_1101: bio2rtl.toml. |
| `examples/serial_pattern_1101/bio_sim/main.c` | Example asset for serial_pattern_1101: main.c. |
| `examples/serial_pattern_1101/serial_pattern_1101.dis` | Example asset for serial_pattern_1101: serial_pattern_1101.dis. |
| `examples/clock_divider_by4_75pct/README.md` | Example asset for clock_divider_by4_75pct: README.md. |
| `examples/clock_divider_by4_75pct/bio2rtl.toml` | Example asset for clock_divider_by4_75pct: bio2rtl.toml. |
| `examples/clock_divider_by4_75pct/bio_sim/main.c` | Example asset for clock_divider_by4_75pct: main.c. |
| `examples/clock_divider_by4_75pct/clock_divider_by4_75pct.dis` | Example asset for clock_divider_by4_75pct: clock_divider_by4_75pct.dis. |

## Low-level semantic engine (`semantic_frontend/bio2rtl/`)
| File | Role |
|---|---|
| `semantic_frontend/bio2rtl/__init__.py` | Low-level semantic-engine module for   init  ; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/analysis.py` | Low-level semantic-engine module for analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/barrier_retime_analysis.py` | Low-level semantic-engine module for barrier retime analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/bitwidth.py` | Low-level semantic-engine module for bitwidth; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/boundary_range_analysis.py` | Low-level semantic-engine module for boundary range analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/boundary_state_prune.py` | Low-level semantic-engine module for boundary state prune; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/canonical_event_analysis.py` | Low-level semantic-engine module for canonical event analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/canonical_phase_analysis.py` | Low-level semantic-engine module for canonical phase analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/carrier_update.py` | Low-level semantic-engine module for carrier update; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/cfg.py` | Low-level semantic-engine module for cfg; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/cli.py` | Low-level semantic-engine module for cli; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/control_automaton_analysis.py` | Low-level semantic-engine module for control automaton analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/control_expr.py` | Low-level semantic-engine module for control expr; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/control_netlist.py` | Low-level semantic-engine module for control netlist; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/control_phase_analysis.py` | Low-level semantic-engine module for control phase analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/control_predicate_analysis.py` | Low-level semantic-engine module for control predicate analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_abstract_storage.py` | Low-level semantic-engine module for dedicated event abstract storage; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_bit_update_minimization.py` | Low-level semantic-engine module for dedicated event bit update minimization; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_conservative_storage.py` | Low-level semantic-engine module for dedicated event conservative storage; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_derived_state.py` | Low-level semantic-engine module for dedicated event derived state; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_event_semantic_minimization.py` | Low-level semantic-engine module for dedicated event event semantic minimization; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_event_semantic_simulator.py` | Low-level semantic-engine module for dedicated event event semantic simulator; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_expression_minimization.py` | Low-level semantic-engine module for dedicated event expression minimization; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_expression_minimization_verifier.py` | Low-level semantic-engine module for dedicated event expression minimization verifier; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_guard_minimization.py` | Low-level semantic-engine module for dedicated event guard minimization; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_guard_minimization_verifier.py` | Low-level semantic-engine module for dedicated event guard minimization verifier; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_joint_control_domain.py` | Low-level semantic-engine module for dedicated event joint control domain; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_materializer.py` | Low-level semantic-engine module for dedicated event materializer; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_reachability.py` | Low-level semantic-engine module for dedicated event reachability; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_relation_verifier.py` | Low-level semantic-engine module for dedicated event relation verifier; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_semantic_guard_minimization.py` | Low-level semantic-engine module for dedicated event semantic guard minimization; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_semantic_guard_minimization_verifier.py` | Low-level semantic-engine module for dedicated event semantic guard minimization verifier; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_simulator.py` | Low-level semantic-engine module for dedicated event simulator; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_state_minimization.py` | Low-level semantic-engine module for dedicated event state minimization; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_event_sv_emitter.py` | Low-level semantic-engine module for dedicated event sv emitter; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_hardware_ir.py` | Low-level semantic-engine module for dedicated hardware ir; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_hardware_report.py` | Low-level semantic-engine module for dedicated hardware report; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dedicated_transition_core_ir.py` | Low-level semantic-engine module for dedicated transition core ir; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/definition_guard_analysis.py` | Low-level semantic-engine module for definition guard analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/definition_schedule_analysis.py` | Low-level semantic-engine module for definition schedule analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/definition_site_analysis.py` | Low-level semantic-engine module for definition site analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/definition_site_equivalence.py` | Low-level semantic-engine module for definition site equivalence; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/definition_site_ir.py` | Low-level semantic-engine module for definition site ir; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dff_candidates.py` | Low-level semantic-engine module for dff candidates; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/dff_width_infer.py` | Low-level semantic-engine module for dff width infer; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/edge_event_analysis.py` | Low-level semantic-engine module for edge event analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/edge_state.py` | Low-level semantic-engine module for edge state; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/edge_state_semantics.py` | Low-level semantic-engine module for edge state semantics; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/epsilon_transition_analysis.py` | Low-level semantic-engine module for epsilon transition analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_boundary_abstract_storage_analysis.py` | Low-level semantic-engine module for event boundary abstract storage analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_boundary_state_analysis.py` | Low-level semantic-engine module for event boundary state analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_domain_analysis.py` | Low-level semantic-engine module for event domain analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_effect_analysis.py` | Low-level semantic-engine module for event effect analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_hardware_predicate_analysis.py` | Low-level semantic-engine module for event hardware predicate analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_liveness_analysis.py` | Low-level semantic-engine module for event liveness analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_macro_full_analysis.py` | Low-level semantic-engine module for event macro full analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_macro_transition_analysis.py` | Low-level semantic-engine module for event macro transition analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_phase_composition.py` | Low-level semantic-engine module for event phase composition; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_phase_proof.py` | Low-level semantic-engine module for event phase proof; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_transition_ir.py` | Low-level semantic-engine module for event transition ir; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_transition_report.py` | Low-level semantic-engine module for event transition report; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_vector_analysis.py` | Low-level semantic-engine module for event vector analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_vector_decision_dag.py` | Low-level semantic-engine module for event vector decision dag; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_vector_predicate_analysis.py` | Low-level semantic-engine module for event vector predicate analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_vector_sequence_dag.py` | Low-level semantic-engine module for event vector sequence dag; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/event_vector_transition_analysis.py` | Low-level semantic-engine module for event vector transition analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/feasible_event_relation_simulator.py` | Low-level semantic-engine module for feasible event relation simulator; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/feasible_symbolic_executor.py` | Low-level semantic-engine module for feasible symbolic executor; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/feasible_transition_core.py` | Low-level semantic-engine module for feasible transition core; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/final_state.py` | Low-level semantic-engine module for final state; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/fsm_ir.py` | Low-level semantic-engine module for fsm ir; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/functions.py` | Low-level semantic-engine module for functions; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/gpio_effect.py` | Low-level semantic-engine module for gpio effect; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/hardware_behavior_ir.py` | Low-level semantic-engine module for hardware behavior ir; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/hardware_behavior_report.py` | Low-level semantic-engine module for hardware behavior report; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/hardware_enable_analysis.py` | Low-level semantic-engine module for hardware enable analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/hardware_event_partition_analysis.py` | Low-level semantic-engine module for hardware event partition analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/hardware_event_transition_analysis.py` | Low-level semantic-engine module for hardware event transition analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/hardware_object_analysis.py` | Low-level semantic-engine module for hardware object analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/hardware_pattern.py` | Low-level semantic-engine module for hardware pattern; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/hardware_predicate_analysis.py` | Low-level semantic-engine module for hardware predicate analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/hardware_temporal_region.py` | Low-level semantic-engine module for hardware temporal region; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/inline_calls.py` | Low-level semantic-engine module for inline calls; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/ir.py` | Low-level semantic-engine module for ir; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/liveout_hoist_analysis.py` | Low-level semantic-engine module for liveout hoist analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/logical_state.py` | Low-level semantic-engine module for logical state; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/loop_bound.py` | Low-level semantic-engine module for loop bound; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/lower.py` | Low-level semantic-engine module for lower; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/next_state_expr.py` | Low-level semantic-engine module for next state expr; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/observable_cone.py` | Low-level semantic-engine module for observable cone; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/observation_storage_specialization.py` | Low-level semantic-engine module for observation storage specialization; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/optimization_cache.py` | Low-level semantic-engine module for optimization cache; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/parser.py` | Low-level semantic-engine module for parser; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/physical_state_diagnostic.py` | Low-level semantic-engine module for physical state diagnostic; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/polling_phase_event_analysis.py` | Low-level semantic-engine module for polling phase event analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/proven_hoist_plan.py` | Low-level semantic-engine module for proven hoist plan; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/reachable_control.py` | Low-level semantic-engine module for reachable control; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/register_ssa.py` | Low-level semantic-engine module for register ssa; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/riscv.py` | Low-level semantic-engine module for riscv; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/rtl_ir.py` | Low-level semantic-engine module for rtl ir; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/sample_barrier_analysis.py` | Low-level semantic-engine module for sample barrier analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/sample_barrier_fusion_analysis.py` | Low-level semantic-engine module for sample barrier fusion analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/scheduler_recovery.py` | Low-level semantic-engine module for scheduler recovery; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/selective_hoist_analysis.py` | Low-level semantic-engine module for selective hoist analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/semantic_boundary.py` | Low-level semantic-engine module for semantic boundary; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/semantic_microstate.py` | Low-level semantic-engine module for semantic microstate; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/semantic_next_state.py` | Low-level semantic-engine module for semantic next state; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/semantic_reach.py` | Low-level semantic-engine module for semantic reach; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/semantic_ssa.py` | Low-level semantic-engine module for semantic ssa; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/semantic_state_role_analysis.py` | Low-level semantic-engine module for semantic state role analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/semantic_systemverilog.py` | Low-level semantic-engine module for semantic systemverilog; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/semantic_transition.py` | Low-level semantic-engine module for semantic transition; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/ssa_opt.py` | Low-level semantic-engine module for ssa opt; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/stack_lowering.py` | Low-level semantic-engine module for stack lowering; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/stack_ssa.py` | Low-level semantic-engine module for stack ssa; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/state_extract.py` | Low-level semantic-engine module for state extract; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/state_opt.py` | Low-level semantic-engine module for state opt; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/state_ssa.py` | Low-level semantic-engine module for state ssa; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/structural_netlist.py` | Low-level semantic-engine module for structural netlist; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/wait_poll_elimination_analysis.py` | Low-level semantic-engine module for wait poll elimination analysis; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/width_trace.py` | Low-level semantic-engine module for width trace; used internally by the production frontend. |
| `semantic_frontend/bio2rtl/write_guard_analysis.py` | Low-level semantic-engine module for write guard analysis; used internally by the production frontend. |

## Production semantic pipeline tools (`semantic_frontend/tools/`)
| File | Role |
|---|---|
| `semantic_frontend/tools/analyze_behavioral_control_quotient.py` | Production semantic-pipeline helper for analyze behavioral control quotient; not a normal user entry point. |
| `semantic_frontend/tools/analyze_corrected_abstract_storage.py` | Production semantic-pipeline helper for analyze corrected abstract storage; not a normal user entry point. |
| `semantic_frontend/tools/analyze_correlated_state_groups.py` | Production semantic-pipeline helper for analyze correlated state groups; not a normal user entry point. |
| `semantic_frontend/tools/analyze_dedicated_event_derived_state_parallel.py` | Production semantic-pipeline helper for analyze dedicated event derived state parallel; not a normal user entry point. |
| `semantic_frontend/tools/analyze_full_boolean_sv_resub.py` | Production semantic-pipeline helper for analyze full boolean sv resub; not a normal user entry point. |
| `semantic_frontend/tools/analyze_history_coalescing.py` | Production semantic-pipeline helper for analyze history coalescing; not a normal user entry point. |
| `semantic_frontend/tools/analyze_joint_control_domain.py` | Production semantic-pipeline helper for analyze joint control domain; not a normal user entry point. |
| `semantic_frontend/tools/analyze_observation_local_packed_bit_specialization.py` | Production semantic-pipeline helper for analyze observation local packed bit specialization; not a normal user entry point. |
| `semantic_frontend/tools/analyze_observation_local_packed_bit_specialization_v2.py` | Production semantic-pipeline helper for analyze observation local packed bit specialization v2; not a normal user entry point. |
| `semantic_frontend/tools/analyze_observation_local_state_specialization.py` | Production semantic-pipeline helper for analyze observation local state specialization; not a normal user entry point. |
| `semantic_frontend/tools/analyze_observation_storage_projection_v2.py` | Production semantic-pipeline helper for analyze observation storage projection v2; not a normal user entry point. |
| `semantic_frontend/tools/analyze_phase_local_state_elision.py` | Production semantic-pipeline helper for analyze phase local state elision; not a normal user entry point. |
| `semantic_frontend/tools/analyze_phase_local_state_elision_v2.py` | Production semantic-pipeline helper for analyze phase local state elision v2; not a normal user entry point. |
| `semantic_frontend/tools/analyze_protocol_counter_ownership.py` | Production semantic-pipeline helper for analyze protocol counter ownership; not a normal user entry point. |
| `semantic_frontend/tools/analyze_protocol_counter_ownership_generic.py` | Production semantic-pipeline helper for analyze protocol counter ownership generic; not a normal user entry point. |
| `semantic_frontend/tools/analyze_register_bit_liveness.py` | Production semantic-pipeline helper for analyze register bit liveness; not a normal user entry point. |
| `semantic_frontend/tools/annotate_corrected_abstract_storage.py` | Production semantic-pipeline helper for annotate corrected abstract storage; not a normal user entry point. |
| `semantic_frontend/tools/build_behavioral_direct_table.py` | Production semantic-pipeline helper for build behavioral direct table; not a normal user entry point. |
| `semantic_frontend/tools/build_correlated_group_encoded_sv.py` | Production semantic-pipeline helper for build correlated group encoded sv; not a normal user entry point. |
| `semantic_frontend/tools/build_dead_high_bit_elided_sv.py` | Production semantic-pipeline helper for build dead high bit elided sv; not a normal user entry point. |
| `semantic_frontend/tools/build_derived47_candidate.py` | Production semantic-pipeline helper for build derived47 candidate; not a normal user entry point. |
| `semantic_frontend/tools/build_history_coalesced_sv.py` | Production semantic-pipeline helper for build history coalesced sv; not a normal user entry point. |
| `semantic_frontend/tools/build_jointctrl_cached.py` | Production semantic-pipeline helper for build jointctrl cached; not a normal user entry point. |
| `semantic_frontend/tools/build_legal_qualified_edge_product.py` | Production semantic-pipeline helper for build legal qualified edge product; not a normal user entry point. |
| `semantic_frontend/tools/build_observation_local_packed_bit_specialized_sv.py` | Production semantic-pipeline helper for build observation local packed bit specialized sv; not a normal user entry point. |
| `semantic_frontend/tools/build_observation_local_state_specialized_sv.py` | Production semantic-pipeline helper for build observation local state specialized sv; not a normal user entry point. |
| `semantic_frontend/tools/build_observation_storage_projected_sv.py` | Production semantic-pipeline helper for build observation storage projected sv; not a normal user entry point. |
| `semantic_frontend/tools/build_packedmask_storage_candidate.py` | Production semantic-pipeline helper for build packedmask storage candidate; not a normal user entry point. |
| `semantic_frontend/tools/build_phase_local_state_elided_sv.py` | Production semantic-pipeline helper for build phase local state elided sv; not a normal user entry point. |
| `semantic_frontend/tools/build_protocol_counter_semantic_bridge_sv_v3.py` | Production semantic-pipeline helper for build protocol counter semantic bridge sv v3; not a normal user entry point. |
| `semantic_frontend/tools/dce_generated_decode_sv.py` | Production semantic-pipeline helper for dce generated decode sv; not a normal user entry point. |
| `semantic_frontend/tools/emit_dedicated_event_storage_opt_sv.py` | Production semantic-pipeline helper for emit dedicated event storage opt sv; not a normal user entry point. |
| `semantic_frontend/tools/factor_shift_clear_recurrence_sv.py` | Production semantic-pipeline helper for factor shift clear recurrence sv; not a normal user entry point. |
| `semantic_frontend/tools/map_tr1um_structural_generic.py` | Production semantic-pipeline helper for map tr1um structural generic; not a normal user entry point. |
| `semantic_frontend/tools/map_tr1um_structural_sv.py` | Production semantic-pipeline helper for map tr1um structural sv; not a normal user entry point. |
| `semantic_frontend/tools/map_tr1um_structural_sv_v2.py` | Production semantic-pipeline helper for map tr1um structural sv v2; not a normal user entry point. |
| `semantic_frontend/tools/map_tr1um_structural_sv_v3.py` | Production semantic-pipeline helper for map tr1um structural sv v3; not a normal user entry point. |
| `semantic_frontend/tools/materialize_complete_with_storage_plan.py` | Production semantic-pipeline helper for materialize complete with storage plan; not a normal user entry point. |
| `semantic_frontend/tools/materialize_corrected_dedicated_event.py` | Production semantic-pipeline helper for materialize corrected dedicated event; not a normal user entry point. |
| `semantic_frontend/tools/minimize_corrected_dedicated_event_guards.py` | Production semantic-pipeline helper for minimize corrected dedicated event guards; not a normal user entry point. |
| `semantic_frontend/tools/minimize_dedicated_event_expressions.py` | Production semantic-pipeline helper for minimize dedicated event expressions; not a normal user entry point. |
| `semantic_frontend/tools/promote_corrected_dedicated_event_relation.py` | Production semantic-pipeline helper for promote corrected dedicated event relation; not a normal user entry point. |
| `semantic_frontend/tools/recover_scheduler_template.py` | Production semantic-pipeline helper for recover scheduler template; not a normal user entry point. |
| `semantic_frontend/tools/run_bio2rtl_production.py` | Production stage runner for the low-level semantic frontend. |
| `semantic_frontend/tools/run_dedicated_event_directed_model.py` | Production semantic-pipeline helper for run dedicated event directed model; not a normal user entry point. |
| `semantic_frontend/tools/verify_corrected_abstract_storage.py` | Production semantic-pipeline helper for verify corrected abstract storage; not a normal user entry point. |
| `semantic_frontend/tools/verify_corrected_guard_minimization.py` | Production semantic-pipeline helper for verify corrected guard minimization; not a normal user entry point. |
| `semantic_frontend/tools/verify_correlated_group_candidate.py` | Production semantic-pipeline helper for verify correlated group candidate; not a normal user entry point. |
| `semantic_frontend/tools/verify_dedicated_event_expression_minimization.py` | Production semantic-pipeline helper for verify dedicated event expression minimization; not a normal user entry point. |
| `semantic_frontend/tools/verify_derived47_candidate.py` | Production semantic-pipeline helper for verify derived47 candidate; not a normal user entry point. |
| `semantic_frontend/tools/verify_history_coalesced_sv.py` | Production semantic-pipeline helper for verify history coalesced sv; not a normal user entry point. |
| `semantic_frontend/tools/verify_history_coalescing_model.py` | Production semantic-pipeline helper for verify history coalescing model; not a normal user entry point. |
| `semantic_frontend/tools/verify_jointctrl_cached.py` | Production semantic-pipeline helper for verify jointctrl cached; not a normal user entry point. |
| `semantic_frontend/tools/verify_structural_netlist_equivalence.py` | Production semantic-pipeline helper for verify structural netlist equivalence; not a normal user entry point. |
| `semantic_frontend/tools/verify_structural_strict.py` | Production semantic-pipeline helper for verify structural strict; not a normal user entry point. |

## Technology / physical support (`technology/`)
| File | Role |
|---|---|
| `technology/control_encoding_plan_cache_v1.json` | Technology/mapping/support data: control_encoding_plan_cache_v1.json. |
| `technology/neutral_comb_output_binding_cache_v1.json` | Technology/mapping/support data: neutral_comb_output_binding_cache_v1.json. |
| `technology/neutral_component_contract_cache_v1.json` | Technology/mapping/support data: neutral_component_contract_cache_v1.json. |
| `technology/neutral_component_contract_cache_v2.json` | Technology/mapping/support data: neutral_component_contract_cache_v2.json. |
| `technology/neutral_component_contract_cache_v3.json` | Technology/mapping/support data: neutral_component_contract_cache_v3.json. |
| `technology/neutral_direct_state_interface_binding_cache_v1.json` | Technology/mapping/support data: neutral_direct_state_interface_binding_cache_v1.json. |
| `technology/neutral_event_interface_binding_cache_v1.json` | Technology/mapping/support data: neutral_event_interface_binding_cache_v1.json. |
| `technology/neutral_interface_binding_cache_v1.json` | Technology/mapping/support data: neutral_interface_binding_cache_v1.json. |
| `technology/neutral_load_hold_bank_binding_cache_v1.json` | Technology/mapping/support data: neutral_load_hold_bank_binding_cache_v1.json. |
| `technology/neutral_primitive_recipes_v1.json` | Technology/mapping/support data: neutral_primitive_recipes_v1.json. |
| `technology/neutral_shared_counter_interface_binding_cache_v1.json` | Technology/mapping/support data: neutral_shared_counter_interface_binding_cache_v1.json. |
| `technology/neutral_shift_interface_binding_cache_v1.json` | Technology/mapping/support data: neutral_shift_interface_binding_cache_v1.json. |
| `technology/physical_realization_policy.json` | Technology/mapping/support data: physical_realization_policy.json. |
| `technology/support_recipes_v1.json` | Technology/mapping/support data: support_recipes_v1.json. |
| `technology/tr1um_cell_area.json` | Technology/mapping/support data: tr1um_cell_area.json. |
| `technology/tr1um_cell_spice_pins.json` | Technology/mapping/support data: tr1um_cell_spice_pins.json. |
| `technology/tr1um_component_recipe_cache_v1.json` | Technology/mapping/support data: tr1um_component_recipe_cache_v1.json. |
| `technology/tr1um_hand_support_v1/gpio_io.sch` | Technology/mapping/support data: gpio_io.sch. |
| `technology/tr1um_hand_support_v1/gpio_io.sym` | Technology/mapping/support data: gpio_io.sym. |
| `technology/tr1um_hand_support_v1/por.sch` | Technology/mapping/support data: por.sch. |
| `technology/tr1um_hand_support_v1/por.sym` | Technology/mapping/support data: por.sym. |
| `technology/tr1um_hand_support_v1/sda_io.sch` | Technology/mapping/support data: sda_io.sch. |
| `technology/tr1um_hand_support_v1/sda_io.sym` | Technology/mapping/support data: sda_io.sym. |
| `technology/tr1um_xschem_symbol_geometry.json` | Technology/mapping/support data: tr1um_xschem_symbol_geometry.json. |

## User and CI helper tools (`tools/`)
| File | Role |
|---|---|
| `tools/generate_i2c_electrical_benchmark.py` | User/CI helper tool: generate_i2c_electrical_benchmark.py. |
| `tools/run_bio_sim_example.py` | User/CI helper tool: run_bio_sim_example.py. |
| `tools/test_example_signals.py` | Regression-check generated example signal sequences and I2C directed behavior. |
| `tools/setup_bio2rtl_env.sh` | User/CI helper tool: setup_bio2rtl_env.sh. |
| `tools/test_generic_pass_manager.py` | User/CI helper tool: test_generic_pass_manager.py. |

