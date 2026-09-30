# bio2rtl ファイルガイド

このファイルは、公開repositoryに残した **295ファイルすべて** の役割を説明します。

## まず何を見ればよいか

通常の利用者が最初に見る必要があるのは、基本的に次だけです。

- `README_ja.md` — 導入と通常の使い方
- `docs/SETUP.md` — 環境構築
- `docs/TOML.md` — `bio2rtl.toml`の設定
- `examples/` — 実際に動く5例
- `tools/setup_bio2rtl_env.sh` — 標準環境の準備
- `tools/run_bio_sim_example.py` — C sourceから回路までのdemo

`bio2rtl/`は公開compiler本体です。`semantic_frontend/bio2rtl/`は、RISC-V `.dis`を正確なevent/state semanticsへ変換するために**別process・別Python namespaceで実行する低レベル解析engine**です。同じ`bio2rtl`というpackage名が二つあるのは意図的で、通常ユーザーが後者を直接import/実行する必要はありません。

今回の整理では、過去の探索・audit・release evidence・旧mapper等を削除し、production pathから必要なものを中心に **635ファイルから295ファイル**へ削減しました。

## GitHub / CI
| File | Role |
|---|---|
| `.github/workflows/smoke.yml` | GitHub Actionsの自動テスト。3つのPython版でCLI/configを確認し、Python 3.10では小型例をfull buildまで実行する。 |

## Repository root / production entry points
| File | Role |
|---|---|
| `.gitignore` | Gitへ入れないbuild生成物、仮想環境、cacheなどを指定する。 |
| `CHANGELOG.md` | 各バージョンで何が変わったかを時系列で記録する。 |
| `FILE_GUIDE.md` | このリポジトリの全ファイルを英語で説明するガイド。 |
| `FILE_GUIDE_ja.md` | このリポジトリの全ファイルを日本語で説明するガイド。 |
| `LICENSE` | bio2rtl本体のApache License 2.0本文。 |
| `Makefile` | 主要exampleのcheck/build/benchmarkを短いmakeコマンドで呼ぶためのショートカット。 |
| `README.md` | GitHubで最初に表示する英語README。導入、設定、build、support回路、位置づけを説明する。 |
| `README_ja.md` | READMEの日本語版。 |
| `RELEASE_NOTES.md` | v1.2.1公開版の要点と既知の範囲をまとめる。 |
| `generate_architecture_certificates.py` | semantic artifactsからgeneric optimization/certificate passを実行し、architecture recovery用の証明データをまとめる。 |
| `generate_stage6_core_certificates.py` | control/shiftなどcore architecture候補の証明certificateを生成する。 |
| `generate_stage7_dynamic_certificates.py` | shared counter、observation snapshot、output-enable recurrenceなど動的に発見するcertificateを生成する。 |
| `generate_storage_relations_certificate.py` | 複数storage間の関係、history/mirror共有などを解析してcertificate化する。 |
| `pyproject.toml` | Python package metadata、依存関係、`bio2rtl` CLI entry pointを定義する。 |
| `recover_seedless_architecture.py` | golden回路を入力にせず、semantic modelとcertificateからDedicated Hardware IRを復元する。 |
| `requirements.txt` | Python依存関係を簡易一覧として示す。通常は`pip install -e .`でpyprojectから入る。 |
| `run_compiler.py` | production compilerの主オーケストレータ。semantic解析→証明付きarchitecture recovery→TR-1um mapping→schematic生成を順に実行する。 |
| `run_semantic_frontend.py` | 隔離されたsemantic frontendを複数stageに分けて実行し、後段が使うcanonical semantic artifactsを作る。 |
| `verify_generic_core.py` | core-only buildで、生成cell数・接続・Xschem成果物がPhysical DHIRと一致するか最終確認する。 |
| `verify_generic_fullchip.py` | full-chip buildで、coreに加えてPOR/I/O supportの選択・接続・成果物を最終確認する。 |

## Public compiler package (`bio2rtl/`)
| File | Role |
|---|---|
| `bio2rtl/__init__.py` | root側`bio2rtl` Python packageの初期化ファイル。 |
| `bio2rtl/__main__.py` | `python -m bio2rtl`をCLIへ接続するentry point。 |
| `bio2rtl/boolean_contract.py` | Boolean論理の入出力contractを表現・検証する共通処理。 |
| `bio2rtl/boolean_mapper.py` | Boolean DAGをTR-1umの論理cellへmappingするgeneric fallback mapper。 |
| `bio2rtl/cli.py` | ユーザー向け`bio2rtl init/check/build/benchmark` CLI本体。workspace作成、compiler実行、成果物コピーも担当する。 |
| `bio2rtl/generic_fullchip_export.py` | 推定したI/O種別に応じてPOR/open-drain/GPIO supportをcoreへ接続しfull-chip schematicを生成する。fallback coreも実PDKの`TR-1um_5_stdcell`を参照する。 |
| `bio2rtl/global_semantic_mapper.py` | 複数semantic componentをまとめて論理回路へmappingし、共有可能な論理を扱う。 |
| `bio2rtl/semantic_liveness.py` | 外部出力・保持状態・primitive/latchから逆向きに依存関係を追い、物理的に不要なcombinational roleを除去する。 |
| `bio2rtl/global_semantic_proof.py` | global mapping後の論理がsemantic contractと一致することを検証する。 |
| `bio2rtl/legal_product_schema.py` | 外部eventのlegal productを読む/検証するschemaと互換処理。 |
| `bio2rtl/legal_role_synthesis.py` | semantic roleからgenericなhardware role/論理を合成する。 |
| `bio2rtl/load_hold_discovery.py` | register bankのload/hold recurrenceをsemantic relationから発見する。 |
| `bio2rtl/routed_xschem_export.py` | routing guide付きcanonical Xschem core schematicを生成する。 |
| `bio2rtl/natural_storage_contracts.py` | counter/shift/registerなど自然なstorage recurrenceのcontractを扱う。 |
| `bio2rtl/neutral_bindings.py` | protocol名に依存しないsemantic componentとtechnology recipeのbindingを扱う。 |
| `bio2rtl/neutral_graph.py` | component間依存をprotocol非依存graphとして構築する。 |
| `bio2rtl/no_cert_phase40_mapper.py` | architecture certificateが適用できない場合にsemantic IRから保守的に回路化するfallback path。 |
| `bio2rtl/observation_projection.py` | 観測時だけ必要なstate/dataへのprojectionを計算する。 |
| `bio2rtl/observation_tx_discovery.py` | 観測/送信局面のsemantic recurrenceを発見する。 |
| `bio2rtl/pass_manager.py` | generic optimization passをtransactionalに実行し、PASS/N/A/FAILを管理する。 |
| `bio2rtl/phase40_direct_lowering.py` | canonical phase40 semantic IRを直接hardware-oriented表現へloweringする。 |
| `bio2rtl/physical_interface.py` | semantic I/Oのread/write/direction使用状況からinput/open-drain/bidirectional等の物理I/O役割を推定する。 |
| `bio2rtl/physical_ir.py` | technology mapping後のcell/net/componentを表すPhysical IRとstructural Verilog出力処理。 |
| `bio2rtl/post_architecture_maturation.py` | architecture recovery後にgenericな整理・最適化を追加適用する。 |
| `bio2rtl/predicate_projection.py` | semantic predicateを必要なhardware predicateへprojectionする。 |
| `bio2rtl/project_inputs.py` | `bio2rtl.toml`と`.dis`の場所・schema・user設定を読み込む。 |
| `bio2rtl/proof_bitset.py` | 大きなstate集合/transition集合を効率よく検証するbitset utility。 |
| `bio2rtl/recipe_keys.py` | semantic/logic内容からtechnology recipeを選ぶcontent keyを作る。 |
| `bio2rtl/recover_seedless.py` | semantic certificateからprotocol固有名なしでarchitecture componentを復元する中心処理。 |
| `bio2rtl/semantic_component_manifest.py` | 復元されたsemantic componentとinterfaceをmanifest化する。 |
| `bio2rtl/semantic_contracts.py` | componentごとのtruth table/recurrence contractを生成し、logic minimization候補を比較する。 |
| `bio2rtl/semantic_projection.py` | 元semantic stateから縮約後state/componentへのprojectionを扱う。 |
| `bio2rtl/stage6_generic_certificates.py` | generic architecture certificateを生成する共通実装。 |
| `bio2rtl/tr1um_declarative_mapper.py` | Dedicated Hardware IRをTR-1um standard cellへ宣言的にmappingする現行production mapper。 |
| `bio2rtl/truth_minimize.py` | care/don’t-careを含むtruth tableから最小Boolean cover候補を列挙する。 |
| `bio2rtl/xschem_export.py` | Physical IRからcore SPICEと中間Xschem情報を生成する。中間cell symbolは後段のrouting-aware exporterで不要になり、最終出力には残さない。 |

## Documentation (`docs/`)
| File | Role |
|---|---|
| `docs/CLI.md` | CLIコマンドとoptionの詳細。 |
| `docs/I2C_ELECTRICAL_TEST.md` | 生成したI²C full-chipを電気的に確認するtestbench/波形条件の説明。 |
| `docs/SETUP.md` | 標準WSL環境と依存toolのセットアップ手順。 |
| `docs/TOML.md` | `bio2rtl.toml`の項目、記法、設定例。 |
| `docs/VERIFICATION.md` | bio2rtlがどこで何を検証し、PASSが何を意味するかの説明。 |
| `docs/XSCHEM_OUTPUTS.md` | 生成されるcore/full-chip Xschemファイルの役割と使い分け。 |

## Examples and references (`examples/`)
| File | Role |
|---|---|
| `examples/BIO_SIM_SOURCES.md` | 5つのexampleのC sourceをbio-simへ渡す方法とsource配置を説明する。 |
| `examples/set_after_3_rises/README.md` | `set_after_3_rises` exampleの目的・I/O・実行方法を説明する。 |
| `examples/set_after_3_rises/bio2rtl.toml` | `set_after_3_rises`をbio2rtlで変換するためのpin/clock/TR-1um設定。 |
| `examples/set_after_3_rises/bio_sim/main.c` | `set_after_3_rises`のBaochip BIO用C source。bio-simで`.dis`へcompileする。 |
| `examples/set_after_3_rises/set_after_3_rises.dis` | `set_after_3_rises`のRISC-V disassembly。bio2rtlのsemantic入力としてそのまま使える。 |
| `examples/i2c_gpio_2bit/README.md` | `i2c_gpio_2bit` exampleの目的・I/O・実行方法を説明する。 |
| `examples/i2c_gpio_2bit/bio2rtl.toml` | `i2c_gpio_2bit`をbio2rtlで変換するためのpin/clock/TR-1um設定。 |
| `examples/i2c_gpio_2bit/bio_sim/main.c` | `i2c_gpio_2bit`のBaochip BIO用C source。bio-simで`.dis`へcompileする。 |
| `examples/i2c_gpio_2bit/i2c-gpio-hw.dis` | `i2c_gpio_2bit`のRISC-V disassembly。bio2rtlのsemantic入力としてそのまま使える。 |
| `examples/i2c_gpio_2bit/reference/i2c_electrical_benchmark_expected.json` | I²C electrical benchmarkで期待する外部観測値を記録したreference data。 |
| `examples/i2c_gpio_2bit/reference/i2c_gpio_2bit_fullchip.sym` | I²C reference schematicを呼び出すXschem symbol。 |
| `examples/i2c_gpio_2bit/reference/i2c_gpio_2bit_i2c_benchmark.sch` | I²C exampleの独立reference Xschem schematic。生成結果の確認/比較に使う。 |
| `examples/i2c_gpio_2bit/reference/manual_layout_122/i2c_gpio_core.sch` | I²C exampleの独立reference Xschem schematic。生成結果の確認/比較に使う。 |
| `examples/i2c_gpio_2bit/reference/manual_layout_122/i2c_gpio_core.sym` | I²C reference schematicを呼び出すXschem symbol。 |
| `examples/i2c_gpio_2bit/reference/manual_layout_122/i2c_gpio_fullchip.sch` | I²C exampleの独立reference Xschem schematic。生成結果の確認/比較に使う。 |
| `examples/clock_divider_by4/README.md` | `clock_divider_by4` exampleの目的・I/O・実行方法を説明する。 |
| `examples/clock_divider_by4/bio2rtl.toml` | `clock_divider_by4`をbio2rtlで変換するためのpin/clock/TR-1um設定。 |
| `examples/clock_divider_by4/bio_sim/main.c` | `clock_divider_by4`のBaochip BIO用C source。bio-simで`.dis`へcompileする。 |
| `examples/clock_divider_by4/clock_divider_by4.dis` | `clock_divider_by4`のRISC-V disassembly。bio2rtlのsemantic入力としてそのまま使える。 |
| `examples/serial_pattern_1101/README.md` | `serial_pattern_1101` exampleの目的・I/O・実行方法を説明する。 |
| `examples/serial_pattern_1101/bio2rtl.toml` | `serial_pattern_1101`をbio2rtlで変換するためのpin/clock/TR-1um設定。 |
| `examples/serial_pattern_1101/bio_sim/main.c` | `serial_pattern_1101`のBaochip BIO用C source。bio-simで`.dis`へcompileする。 |
| `examples/serial_pattern_1101/serial_pattern_1101.dis` | `serial_pattern_1101`のRISC-V disassembly。bio2rtlのsemantic入力としてそのまま使える。 |
| `examples/clock_divider_by4_75pct/README.md` | `clock_divider_by4_75pct` exampleの目的・I/O・実行方法を説明する。 |
| `examples/clock_divider_by4_75pct/bio2rtl.toml` | `clock_divider_by4_75pct`をbio2rtlで変換するためのpin/clock/TR-1um設定。 |
| `examples/clock_divider_by4_75pct/bio_sim/main.c` | `clock_divider_by4_75pct`のBaochip BIO用C source。bio-simで`.dis`へcompileする。 |
| `examples/clock_divider_by4_75pct/clock_divider_by4_75pct.dis` | `clock_divider_by4_75pct`のRISC-V disassembly。bio2rtlのsemantic入力としてそのまま使える。 |

## Low-level semantic engine (`semantic_frontend/bio2rtl/`)
| File | Role |
|---|---|
| `semantic_frontend/bio2rtl/__init__.py` |   init  に関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/analysis.py` | RISC-V命令列から基本的なsemantic解析を開始し、各解析結果をまとめる。 |
| `semantic_frontend/bio2rtl/barrier_retime_analysis.py` | barrier retimingを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/bitwidth.py` | 値とstateの必要bit幅を推定・縮約する。 |
| `semantic_frontend/bio2rtl/boundary_range_analysis.py` | boundary rangeを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/boundary_state_prune.py` | boundary stateから不要/到達不能部分を削減する。 |
| `semantic_frontend/bio2rtl/canonical_event_analysis.py` | canonical eventを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/canonical_phase_analysis.py` | canonical phaseを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/carrier_update.py` | 値carrier updateに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/cfg.py` | 命令列からcontrol-flow graphを構築する。 |
| `semantic_frontend/bio2rtl/cli.py` | semantic frontend専用CLI。root CLIから隔離subprocessとして呼ばれる。 |
| `semantic_frontend/bio2rtl/control_automaton_analysis.py` | control automatonを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/control_expr.py` | controlのBoolean/算術式表現を扱う。 |
| `semantic_frontend/bio2rtl/control_netlist.py` | control netlistに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/control_phase_analysis.py` | control phaseを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/control_predicate_analysis.py` | control predicateを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/dedicated_event_abstract_storage.py` | 専用event model abstract storageに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/dedicated_event_bit_update_minimization.py` | 専用event model bit updateを等価性を保ちながら最小化する。 |
| `semantic_frontend/bio2rtl/dedicated_event_conservative_storage.py` | 専用event model conservative storageに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/dedicated_event_derived_state.py` | 専用event model derived stateに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/dedicated_event_event_semantic_minimization.py` | 専用event model event semanticを等価性を保ちながら最小化する。 |
| `semantic_frontend/bio2rtl/dedicated_event_event_semantic_simulator.py` | 専用event model event semanticを直接simulationしてtransition/出力を評価する。 |
| `semantic_frontend/bio2rtl/dedicated_event_expression_minimization.py` | 専用event model expressionを等価性を保ちながら最小化する。 |
| `semantic_frontend/bio2rtl/dedicated_event_expression_minimization_verifier.py` | 専用event model expression minimization変換/最適化が元semanticsと一致するか検証する。 |
| `semantic_frontend/bio2rtl/dedicated_event_guard_minimization.py` | 専用event model guardを等価性を保ちながら最小化する。 |
| `semantic_frontend/bio2rtl/dedicated_event_guard_minimization_verifier.py` | 専用event model guard minimization変換/最適化が元semanticsと一致するか検証する。 |
| `semantic_frontend/bio2rtl/dedicated_event_joint_control_domain.py` | 専用event model joint control domainに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/dedicated_event_materializer.py` | 抽象event relationを具体的なstate/update relationへ展開する。 |
| `semantic_frontend/bio2rtl/dedicated_event_reachability.py` | event駆動semantic modelの到達可能状態を列挙・検証する。 |
| `semantic_frontend/bio2rtl/dedicated_event_relation_verifier.py` | 専用event model relation変換/最適化が元semanticsと一致するか検証する。 |
| `semantic_frontend/bio2rtl/dedicated_event_semantic_guard_minimization.py` | 専用event model semantic guardを等価性を保ちながら最小化する。 |
| `semantic_frontend/bio2rtl/dedicated_event_semantic_guard_minimization_verifier.py` | 専用event model semantic guard minimization変換/最適化が元semanticsと一致するか検証する。 |
| `semantic_frontend/bio2rtl/dedicated_event_simulator.py` | 専用event modelを直接simulationしてtransition/出力を評価する。 |
| `semantic_frontend/bio2rtl/dedicated_event_state_minimization.py` | 専用event model stateを等価性を保ちながら最小化する。 |
| `semantic_frontend/bio2rtl/dedicated_event_sv_emitter.py` | 専用event modelをSystemVerilogとして出力する。 |
| `semantic_frontend/bio2rtl/dedicated_hardware_ir.py` | dedicated hardwareを表すfrontend内部IRデータ構造。 |
| `semantic_frontend/bio2rtl/dedicated_hardware_report.py` | dedicated hardwareの解析結果をreport形式へまとめる。 |
| `semantic_frontend/bio2rtl/dedicated_transition_core_ir.py` | dedicated transition coreを表すfrontend内部IRデータ構造。 |
| `semantic_frontend/bio2rtl/definition_guard_analysis.py` | definition guardを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/definition_schedule_analysis.py` | definition scheduleを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/definition_site_analysis.py` | 値の定義位置を解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/definition_site_equivalence.py` | 値の定義位置 equivalenceに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/definition_site_ir.py` | 値の定義位置を表すfrontend内部IRデータ構造。 |
| `semantic_frontend/bio2rtl/dff_candidates.py` | DFF candidatesに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/dff_width_infer.py` | DFF bit幅 inferに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/edge_event_analysis.py` | edge eventを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/edge_state.py` | edge stateに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/edge_state_semantics.py` | edge state semanticsに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/epsilon_transition_analysis.py` | eventを消費しないε transitionを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_boundary_abstract_storage_analysis.py` | event境界 abstract storageを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_boundary_state_analysis.py` | event境界 stateを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_domain_analysis.py` | event domainを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_effect_analysis.py` | event effectを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_hardware_predicate_analysis.py` | event hardware predicateを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_liveness_analysis.py` | event livenessを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_macro_full_analysis.py` | event macro fullを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_macro_transition_analysis.py` | event macro transitionを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_phase_composition.py` | event phase compositionに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/event_phase_proof.py` | event phase proofに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/event_transition_ir.py` | event transitionを表すfrontend内部IRデータ構造。 |
| `semantic_frontend/bio2rtl/event_transition_report.py` | event transitionの解析結果をreport形式へまとめる。 |
| `semantic_frontend/bio2rtl/event_vector_analysis.py` | 同時event vectorを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_vector_decision_dag.py` | 同時event vector decision dagに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/event_vector_predicate_analysis.py` | 同時event vector predicateを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/event_vector_sequence_dag.py` | 同時event vector sequence dagに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/event_vector_transition_analysis.py` | 同時event vector transitionを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/feasible_event_relation_simulator.py` | feasible event relationを直接simulationしてtransition/出力を評価する。 |
| `semantic_frontend/bio2rtl/feasible_symbolic_executor.py` | 分岐条件を追跡し、実行可能なpath/eventだけをsymbolic executionする。 |
| `semantic_frontend/bio2rtl/feasible_transition_core.py` | 実行可能pathからcanonical transition relationの中心部分を構築する。 |
| `semantic_frontend/bio2rtl/final_state.py` | frontend解析結果から最終的なpersistent state集合をまとめる。 |
| `semantic_frontend/bio2rtl/fsm_ir.py` | FSMを表すfrontend内部IRデータ構造。 |
| `semantic_frontend/bio2rtl/functions.py` | function境界やcall graphに関する解析utility。 |
| `semantic_frontend/bio2rtl/gpio_effect.py` | BIO GPIO special register操作を外部pinへのeffectとして解析する。 |
| `semantic_frontend/bio2rtl/hardware_behavior_ir.py` | hardware behaviorを表すfrontend内部IRデータ構造。 |
| `semantic_frontend/bio2rtl/hardware_behavior_report.py` | hardware behaviorの解析結果をreport形式へまとめる。 |
| `semantic_frontend/bio2rtl/hardware_enable_analysis.py` | hardware enableを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/hardware_event_partition_analysis.py` | hardware event partitionを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/hardware_event_transition_analysis.py` | hardware event transitionを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/hardware_object_analysis.py` | hardware objectを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/hardware_pattern.py` | hardware patternに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/hardware_predicate_analysis.py` | hardware predicateを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/hardware_temporal_region.py` | hardware temporal regionに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/inline_calls.py` | 小さな関数callをsemantic解析しやすい形へinline化する。 |
| `semantic_frontend/bio2rtl/ir.py` | frontendで共通使用する基本IRデータ構造。 |
| `semantic_frontend/bio2rtl/liveout_hoist_analysis.py` | live-out hoistを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/logical_state.py` | 論理state表現とstate bitの役割を扱う。 |
| `semantic_frontend/bio2rtl/loop_bound.py` | loopの解析可能なbound/反復構造を推定する。 |
| `semantic_frontend/bio2rtl/lower.py` | parseした命令をfrontend内部IRへloweringする。 |
| `semantic_frontend/bio2rtl/next_state_expr.py` | next stateのBoolean/算術式表現を扱う。 |
| `semantic_frontend/bio2rtl/observable_cone.py` | 外部観測coneに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/observation_storage_specialization.py` | 観測 storage specializationに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/optimization_cache.py` | optimization cacheに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/parser.py` | objdump形式のRISC-V `.dis`を命令・label・operandへparseする。 |
| `semantic_frontend/bio2rtl/physical_state_diagnostic.py` | physical state diagnosticに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/polling_phase_event_analysis.py` | polling phase eventを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/proven_hoist_plan.py` | proven hoist planに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/reachable_control.py` | reachable controlに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/register_ssa.py` | registerをSSA形式へ変換・整理する。 |
| `semantic_frontend/bio2rtl/riscv.py` | bio2rtlが扱うRISC-V命令とBIO special registerの基本semanticsを定義する。 |
| `semantic_frontend/bio2rtl/rtl_ir.py` | RTL出力前の中間表現を定義する。 |
| `semantic_frontend/bio2rtl/sample_barrier_analysis.py` | sample barrierを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/sample_barrier_fusion_analysis.py` | sample barrier fusionを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/scheduler_recovery.py` | CPUのpolling/control flowから外部event境界のscheduler構造を復元する。 |
| `semantic_frontend/bio2rtl/selective_hoist_analysis.py` | 選択的hoistを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/semantic_boundary.py` | semantic boundaryに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/semantic_microstate.py` | semantic microstateに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/semantic_next_state.py` | semantic next stateに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/semantic_reach.py` | semantic reachに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/semantic_ssa.py` | semanticをSSA形式へ変換・整理する。 |
| `semantic_frontend/bio2rtl/semantic_state_role_analysis.py` | semantic state roleを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/semantic_systemverilog.py` | semantic reference modelのSystemVerilogを生成する。 |
| `semantic_frontend/bio2rtl/semantic_transition.py` | semantic transitionに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/ssa_opt.py` | ssaにgenericな最適化を適用する。 |
| `semantic_frontend/bio2rtl/stack_lowering.py` | stack loweringに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/stack_ssa.py` | stackをSSA形式へ変換・整理する。 |
| `semantic_frontend/bio2rtl/state_extract.py` | state extractに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/state_opt.py` | stateにgenericな最適化を適用する。 |
| `semantic_frontend/bio2rtl/state_ssa.py` | stateをSSA形式へ変換・整理する。 |
| `semantic_frontend/bio2rtl/structural_netlist.py` | frontend側で構造netlist表現を構築する。 |
| `semantic_frontend/bio2rtl/wait_poll_elimination_analysis.py` | wait poll eliminationを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |
| `semantic_frontend/bio2rtl/width_trace.py` | bit幅 traceに関するsemantic frontend内部処理。通常ユーザーが直接実行するファイルではない。 |
| `semantic_frontend/bio2rtl/write_guard_analysis.py` | write guardを解析し、後段の縮約・architecture recoveryに使う情報を生成する。 |

## Production semantic pipeline tools (`semantic_frontend/tools/`)
| File | Role |
|---|---|
| `semantic_frontend/tools/analyze_behavioral_control_quotient.py` | production pipeline内で`behavioral control quotient`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_corrected_abstract_storage.py` | production pipeline内で`corrected abstract storage`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_correlated_state_groups.py` | production pipeline内で`correlated state groups`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_dedicated_event_derived_state_parallel.py` | production pipeline内で`dedicated event derived state parallel`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_full_boolean_sv_resub.py` | production pipeline内で`full boolean sv resub`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_history_coalescing.py` | production pipeline内で`history coalescing`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_joint_control_domain.py` | production pipeline内で`joint control domain`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_observation_local_packed_bit_specialization.py` | production pipeline内で`observation local packed bit specialization`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_observation_local_packed_bit_specialization_v2.py` | production pipeline内で`observation local packed bit specialization v2`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_observation_local_state_specialization.py` | production pipeline内で`observation local state specialization`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_observation_storage_projection_v2.py` | production pipeline内で`observation storage projection v2`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_phase_local_state_elision.py` | production pipeline内で`phase local state elision`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_phase_local_state_elision_v2.py` | production pipeline内で`phase local state elision v2`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_protocol_counter_ownership.py` | production pipeline内で`protocol counter ownership`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_protocol_counter_ownership_generic.py` | production pipeline内で`protocol counter ownership generic`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/analyze_register_bit_liveness.py` | production pipeline内で`register bit liveness`を解析し、次の変換候補/証明入力を作る。 |
| `semantic_frontend/tools/annotate_corrected_abstract_storage.py` | `corrected abstract storage`の解析情報を既存IRへ注記する。 |
| `semantic_frontend/tools/build_behavioral_direct_table.py` | 解析結果から`behavioral direct table`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_correlated_group_encoded_sv.py` | 解析結果から`correlated group encoded sv`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_dead_high_bit_elided_sv.py` | 解析結果から`dead high bit elided sv`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_derived47_candidate.py` | 解析結果から`derived47 candidate`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_history_coalesced_sv.py` | 解析結果から`history coalesced sv`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_jointctrl_cached.py` | 解析結果から`jointctrl cached`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_legal_qualified_edge_product.py` | 解析結果から`legal qualified edge product`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_observation_local_packed_bit_specialized_sv.py` | 解析結果から`observation local packed bit specialized sv`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_observation_local_state_specialized_sv.py` | 解析結果から`observation local state specialized sv`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_observation_storage_projected_sv.py` | 解析結果から`observation storage projected sv`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_packedmask_storage_candidate.py` | 解析結果から`packedmask storage candidate`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_phase_local_state_elided_sv.py` | 解析結果から`phase local state elided sv`候補または中間表現を生成する。 |
| `semantic_frontend/tools/build_protocol_counter_semantic_bridge_sv_v3.py` | 解析結果から`protocol counter semantic bridge sv v3`候補または中間表現を生成する。 |
| `semantic_frontend/tools/dce_generated_decode_sv.py` | `generated decode sv`にdead-code eliminationを適用する。 |
| `semantic_frontend/tools/emit_dedicated_event_storage_opt_sv.py` | `dedicated event storage opt sv`を後段が読める形式で出力する。 |
| `semantic_frontend/tools/factor_shift_clear_recurrence_sv.py` | `shift clear recurrence sv`の論理を因数分解しcell数削減候補を作る。 |
| `semantic_frontend/tools/map_tr1um_structural_generic.py` | `tr1um structural generic`として論理をstandard-cell構造へmappingするproduction補助script。 |
| `semantic_frontend/tools/map_tr1um_structural_sv.py` | `tr1um structural sv`として論理をstandard-cell構造へmappingするproduction補助script。 |
| `semantic_frontend/tools/map_tr1um_structural_sv_v2.py` | `tr1um structural sv v2`として論理をstandard-cell構造へmappingするproduction補助script。 |
| `semantic_frontend/tools/map_tr1um_structural_sv_v3.py` | `tr1um structural sv v3`として論理をstandard-cell構造へmappingするproduction補助script。 |
| `semantic_frontend/tools/materialize_complete_with_storage_plan.py` | 抽象relation/planから`complete with storage plan`を具体的なIR/transitionへ展開する。 |
| `semantic_frontend/tools/materialize_corrected_dedicated_event.py` | 抽象relation/planから`corrected dedicated event`を具体的なIR/transitionへ展開する。 |
| `semantic_frontend/tools/minimize_corrected_dedicated_event_guards.py` | `corrected dedicated event guards`をsemantic equivalenceを保ちながら縮約する。 |
| `semantic_frontend/tools/minimize_dedicated_event_expressions.py` | `dedicated event expressions`をsemantic equivalenceを保ちながら縮約する。 |
| `semantic_frontend/tools/promote_corrected_dedicated_event_relation.py` | 検証済みの`corrected dedicated event relation`を次段のcanonical authorityへ昇格する。 |
| `semantic_frontend/tools/recover_scheduler_template.py` | `scheduler template`をsemantic dataから復元する。 |
| `semantic_frontend/tools/run_bio2rtl_production.py` | semantic frontendのproduction stage runner。各解析・変換・verify scriptをcontent-addressed stageとして順番に実行する。 |
| `semantic_frontend/tools/run_dedicated_event_directed_model.py` | 専用event modelを既知のdirected sequenceで駆動する検証用model runner。history/storage proofからも利用される。 |
| `semantic_frontend/tools/verify_corrected_abstract_storage.py` | `corrected abstract storage`が元semantic relationと等価か、または構造条件を満たすか検証する。 |
| `semantic_frontend/tools/verify_corrected_guard_minimization.py` | `corrected guard minimization`が元semantic relationと等価か、または構造条件を満たすか検証する。 |
| `semantic_frontend/tools/verify_correlated_group_candidate.py` | `correlated group candidate`が元semantic relationと等価か、または構造条件を満たすか検証する。 |
| `semantic_frontend/tools/verify_dedicated_event_expression_minimization.py` | `dedicated event expression minimization`が元semantic relationと等価か、または構造条件を満たすか検証する。 |
| `semantic_frontend/tools/verify_derived47_candidate.py` | `derived47 candidate`が元semantic relationと等価か、または構造条件を満たすか検証する。 |
| `semantic_frontend/tools/verify_history_coalesced_sv.py` | `history coalesced sv`が元semantic relationと等価か、または構造条件を満たすか検証する。 |
| `semantic_frontend/tools/verify_history_coalescing_model.py` | `history coalescing model`が元semantic relationと等価か、または構造条件を満たすか検証する。 |
| `semantic_frontend/tools/verify_jointctrl_cached.py` | `jointctrl cached`が元semantic relationと等価か、または構造条件を満たすか検証する。 |
| `semantic_frontend/tools/verify_structural_netlist_equivalence.py` | `structural netlist equivalence`が元semantic relationと等価か、または構造条件を満たすか検証する。 |
| `semantic_frontend/tools/verify_structural_strict.py` | `structural strict`が元semantic relationと等価か、または構造条件を満たすか検証する。 |

## Technology / physical support (`technology/`)
| File | Role |
|---|---|
| `technology/control_encoding_plan_cache_v1.json` | control state encodingの検証済みgeneric recipe/cache。内容一致時だけ再利用する。 |
| `technology/neutral_comb_output_binding_cache_v1.json` | 組合せ出力componentのprotocol非依存binding cache。 |
| `technology/neutral_component_contract_cache_v1.json` | generic semantic component contract cacheの旧互換版v1。 |
| `technology/neutral_component_contract_cache_v2.json` | generic semantic component contract cache v2。 |
| `technology/neutral_component_contract_cache_v3.json` | 現行generic semantic component contract cache v3。 |
| `technology/neutral_direct_state_interface_binding_cache_v1.json` | direct-state componentと物理interfaceのneutral binding cache。 |
| `technology/neutral_event_interface_binding_cache_v1.json` | event signalと物理interfaceのneutral binding cache。 |
| `technology/neutral_interface_binding_cache_v1.json` | generic component/interface bindingの共通cache。 |
| `technology/neutral_load_hold_bank_binding_cache_v1.json` | load/hold bankのgeneric interface binding cache。 |
| `technology/neutral_primitive_recipes_v1.json` | counter/register/logic等のprotocol非依存primitive recipe定義。 |
| `technology/neutral_shared_counter_interface_binding_cache_v1.json` | shared counter componentのinterface binding cache。 |
| `technology/neutral_shift_interface_binding_cache_v1.json` | shift/serializer componentのinterface binding cache。 |
| `technology/physical_realization_policy.json` | TR-1umへの物理実装時に許すclock/polarity等のpolicy。 |
| `technology/support_recipes_v1.json` | POR、input、open-drain、bidirectional GPIOなどfull-chip support recipeの定義。 |
| `technology/tr1um_cell_area.json` | TR-1um standard cellごとの実面積。mapperのcost評価に使用する。 |
| `technology/tr1um_cell_spice_pins.json` | TR-1um standard cellのSPICE端子順とpin情報。 |
| `technology/tr1um_component_recipe_cache_v1.json` | semantic componentをTR-1um cell構成へ落とす検証済みrecipe cache。 |
| `technology/tr1um_hand_support_v1/gpio_io.sch` | bidirectional/push-pull GPIO用の手書きTR-1um support schematic。 |
| `technology/tr1um_hand_support_v1/gpio_io.sym` | `gpio_io.sch`をfull-chipから呼ぶXschem symbol。 |
| `technology/tr1um_hand_support_v1/por.sch` | 電源投入resetを生成する手書きTR-1um POR schematic。 |
| `technology/tr1um_hand_support_v1/por.sym` | `por.sch`をfull-chipから呼ぶXschem symbol。 |
| `technology/tr1um_hand_support_v1/sda_io.sch` | open-drain出力＋入力buffer用の手書きTR-1um I/O schematic。 |
| `technology/tr1um_hand_support_v1/sda_io.sym` | `sda_io.sch`をfull-chipから呼ぶXschem symbol。 |
| `technology/tr1um_xschem_symbol_geometry.json` | 各TR-1um Xschem symbolの実寸・pin位置情報。routing-aware exporterが使用する。 |

## User and CI helper tools (`tools/`)
| File | Role |
|---|---|
| `tools/generate_i2c_electrical_benchmark.py` | build済みI²C full-chipに対するXschem電気testbenchと期待値を生成する。 |
| `tools/run_bio_sim_example.py` | 同梱C sourceをbio-simで`.dis`化し、そのままbio2rtl check/buildまで実行するdemo runner。 |
| `tools/test_example_signals.py` | 生成後の各サンプル信号列とI²C directed transactionを回帰確認する。 |
| `tools/setup_bio2rtl_env.sh` | 標準WSL上にvenv、bio2rtl、ziglang、RISC-V objdump、bio-simを準備するsetup script。 |
| `tools/test_generic_pass_manager.py` | optional generic passがPASS/N/A/FAILとして安全に扱われるか確認する小型回帰test。 |

