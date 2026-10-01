# bio2rtl

English: [README.md](README.md)

ファイル構成: [FILE_GUIDE_ja.md](FILE_GUIDE_ja.md) / [English](FILE_GUIDE.md)

bio2rtlは、**Baochip BIOで実行するプログラムの`.dis`を解析し、同じ外部I/O動作を行うTR-1um標準セル回路を生成するコンパイラ**です。生成する回路にはBIOのCPUを含めません。

入力はBaochip公式`bio-sim`が生成した`.dis`と`bio2rtl.toml`です。標準のbuildでは、TR-1um standard-cell netlistとXschem coreを生成します。`[physical_support]`を指定した場合は、PORとI/O support回路を接続したfull-chip schematicも生成します。

## Quick demo

TR-1um開発の標準環境には、ISHI会の[`OpenEDA-PDK_SetupScript`](https://github.com/ishi-kai/OpenEDA-PDK_SetupScript)で配布されている**OpenSUSI-TR10用WSLイメージ**を使用します。

### 1. WSLイメージを登録する

`OpenEDA-PDK_SetupScript`のREADMEにある **“Image for WSL version of OpenSUSI-TR10 (Tokai Rika)”** を取得し、Windows PowerShellから登録します。

```powershell
wsl --import-in-place ubuntu2204_ishi-kai_EDA .\ubuntu2204_ishi-kai_EDA\ext4.vhdx
wsl -d ubuntu2204_ishi-kai_EDA
```

### 2. bio2rtlを取得する

WSL内で実行します。

```bash
git clone https://github.com/doraneko94/bio2rtl.git
cd bio2rtl
```

### 3. 開発環境を準備する

```bash
bash tools/setup_bio2rtl_env.sh
source .venv/bin/activate
```

`setup_bio2rtl_env.sh`はPython virtual environmentを作成し、bio2rtl、RISC-V `objdump`、`ziglang`をセットアップします。`../bio-sim`にBaochip公式`bio-sim`がなければ、その場所へcloneします。

### 4. サンプルを変換する

```bash
python tools/run_bio_sim_example.py ../bio-sim set_after_3_rises
```

このコマンドは次の順に処理します。

```text
main.c → bio-sim → .dis → bio2rtl → TR-1um回路
```

5つのサンプルをすべて実行する場合は次を使います。

```bash
python tools/run_bio_sim_example.py ../bio-sim --all
python tools/test_example_signals.py
```

## 同梱サンプル

| Example | 動作 | v1.2.1の生成結果 |
|---|---|---:|
| `set_after_3_rises` | `TICK`の3回目の立上り後に`OUT=1`を保持 | 16 cells |
| `clock_divider_by4_75pct` | 4周期中3周期をHighにする周期出力 | 11 cells |
| `serial_pattern_1101` | `1101`のserial patternを出力 | 29 cells |
| `clock_divider_by4` | 4分周・50% dutyの周期出力 | 12 cells |
| `i2c_gpio_2bit` | I²Cで制御する2-bit GPIO expander | 122 cells |

各サンプルのBIO Cソースは`examples/<name>/bio_sim/main.c`、同梱`.dis`は`examples/<name>/`にあります。

## 自分のBIOプログラムを変換する

### 1. `bio-sim`で`.dis`を生成する

BIO Cソースを`bio-sim/sw/<module>/main.c`に置きます。

```bash
cd ../bio-sim/sw
python -m ziglang build "-Dmodule=<module>"
```

`my_module`を指定した場合、通常は次のファイルが生成されます。

```text
../bio-sim/sw/my_module/my_module.dis
```

bio2rtlリポジトリへ戻ります。

```bash
cd ../../bio2rtl
```

### 2. `bio2rtl.toml`で外部I/Oを指定する

bio2rtlは`.dis`から内部動作を解析します。ユーザーは`bio2rtl.toml`で、外部pinとBIO GPIOの対応、状態遷移を進める外部pin、physical supportを使用するかを指定します。

BIO GPIO16を`TICK`、GPIO18を`OUT`として使用し、`TICK`のedgeで状態が進む例は次のとおりです。

```toml
schema = "bio2rtl-project-v1.1"
program = "my_module.dis"
name = "my_project"

[[io]]
name = "TICK"
gpio = 16
support = "direct"

[[io]]
name = "OUT"
gpio = 18
support = "gpio_o"

[clock]
mode = "io_edges"
source = "TICK"

[physical_support]
technology = "TR-1um"
```

| TOML項目 | 内容 |
|---|---|
| `program` | 変換する`.dis`のファイル名 |
| `name` | project名 |
| `[[io]].name` | 外部pin名 |
| `[[io]].gpio` | 対応するBIO GPIO番号 |
| `[[io]].support` | `auto`、`direct`、`sda_io`、`gpio_io`、`gpio_o`。省略時は`auto` |
| `[clock].mode` | 現在は`io_edges`を使用 |
| `[clock].source` | 状態遷移を進める外部I/O |
| `[physical_support]` | POR/I/O supportを含むfull-chipを生成するときに指定 |

input/outputの方向は`.dis`から推定します。`support=auto`では、推定したI/O用途に対応するsupport回路を選択します。明示したsupportと推定結果が矛盾した場合はbuildを停止します。

`[clock].source`には、BIOプログラムの状態変化を進める実際の外部I/Oを指定します。bio2rtlはCPU pollingだけを根拠にfree-running hardware clockを追加しません。

### 3. `bio2rtl init`でTOMLを生成する

```bash
bio2rtl init <path-to-program.dis> --io TICK=16 --io OUT=18 --clock TICK -n my_project
```

coreだけを生成する設定ファイルは`--core-only`で作成できます。

```bash
bio2rtl init <path-to-program.dis> --io TICK=16 --io OUT=18 --clock TICK -n my_project --core-only
```

利用可能なオプションは次で確認できます。

```bash
bio2rtl init --help
```

### 4. 設定確認とbuild

```bash
bio2rtl check <path-to-program.dis>
bio2rtl build <path-to-program.dis>
```

`check`は`.dis`、TOML、GPIO割当、clock source、support指定の整合性を確認します。`build`はsemantic解析、回路最適化、TR-1um mapping、検証、netlist/Xschem出力まで実行します。

`bio2rtl.toml`を`.dis`と別の場所に置く場合は`-c`で指定します。

```bash
bio2rtl build <path-to-program.dis> -c path/to/bio2rtl.toml
```

## 生成物

主な生成物は次のとおりです。

| ファイル | 内容 |
|---|---|
| `build/<project>.structural.v` | TR-1um standard cellで構成したstructural Verilog |
| `build/xschem/<project>_core.sch` | 配線済みのcanonical core schematic |
| `build/xschem/<project>_core.sym` | core symbol |
| `build/xschem/<project>_tb.sym` | testbench用symbol |
| `build/GLOBAL_SEMANTIC_MAP_PROOF.json` | semantic mapping proof |
| `build/SELECTED_PHYSICAL_MAP_PROOF.json` | 採用したphysical mappingのproof |

`[physical_support]`を指定した場合は、次も生成します。

| ファイル | 内容 |
|---|---|
| `build/xschem/<project>_fullchip.sch` | core、POR、I/O supportを接続したschematic |
| `build/xschem/<project>_fullchip.sym` | full-chip symbol |
| `build/xschem/support/` | buildで使用したsupport回路 |

v1.2.1では`*_manual.sch`と`*_core_phy.*`を生成しません。TR-1um standard-cell coreは`*_core.sch`に統一しています。

## Core-only build

TOMLから`[physical_support]`を削除すると、TR-1um standard-cell coreだけを生成します。

```toml
# [physical_support] を記述しない
```

この設定でもsemantic解析、TR-1um mapping、core schematic生成、mapping proofを実行します。POR、`sda_io`、`gpio_io`、`gpio_o`、full-chip schematicは生成しません。

v1.2.1では、core mappingがphysical-support recipeに依存しないように処理を分離しました。同じ`.dis`とI/O設定を使った場合、physical supportの有無でcanonical coreの論理内容は変わりません。

## TR-1um physical support

次の設定を追加すると、digital coreにPORとI/O supportを接続します。

```toml
[physical_support]
technology = "TR-1um"
```

現在のsupport回路は`technology/tr1um_hand_support_v1/`にあります。

| 回路 | 使用条件 | 主な構成 |
|---|---|---|
| `por.sch` | power-on reset | RR: W=2.8 µm / L=60 µm ×8、CSIO: 110 × 110 µm ×4、INV_X2、BUF_X4 |
| `sda_io.sch` | open-drain出力とpin入力が必要 | BUF_X1、BUF_X2、BUF_X4、NMOS: W=30 µm / L=1 µm / m=9 |
| `gpio_io.sch` | push-pull出力とreadbackの両方が必要 | NAND2、AND2_X1、BUF_X1、BUF_X2、PMOS: W=30 µm / L=1 µm / m=27、NMOS: W=30 µm / L=1 µm / m=9 |
| `gpio_o.sch` | readback不要のpush-pull出力 | NAND2、AND2_X1、PMOS: W=30 µm / L=1 µm / m=27、NMOS: W=30 µm / L=1 µm / m=9 |

I/Oごとの接続は次のようになります。

| I/O用途 | 接続 |
|---|---|
| 通常input | 外部pinをcore inputへ直接接続 |
| open-drain | `PAD`を外部pin、`IN`をcore入力、`LOW`をdrive-low制御へ接続 |
| readback付きpush-pull / bidirectional | `gpio_io`を使用し、`INPUT_VALUE`をcoreへ返す |
| readback不要のpush-pull | `gpio_o`を使用し、`OUT/OUT_B`と`DIR/DIR_B`でoutput dataとoutput-enableを制御 |

`i2c_gpio_2bit`ではSCLに`direct`、SDAに`sda_io`、GPIO0/1に`gpio_io`を使用します。

現在の`fullchip`が自動生成する範囲はdigital core、POR、I/O supportまでです。ESD回路とpad ringは生成しません。

## bio2rtlが行う処理

bio2rtlは`.dis`から外部I/Oの変化と内部状態の関係を解析し、その動作を専用の状態回路と組合せ回路へ変換します。主な処理は次のとおりです。

- **Feasible symbolic execution / event semantics**: 実行可能な分岐とI/O eventを抽出する
- **Reachable-state reduction**: 到達しない状態を除外する
- **State quotienting / storage reduction**: 外部動作が同じ状態を統合し、必要なstorageを求める
- **Architecture recovery**: counter、shift register、FSM、latch、GPIO data/directionなどの構造を抽出する
- **Boolean minimization**: care/don't-care条件を使って論理式を縮小する
- **Global logic sharing**: 複数の出力で共通する論理を共有する
- **Dead-cone elimination**: 保存状態と外部出力のどちらにも影響しない組合せ論理を除去する
- **TR-1um mapping**: 生成した論理をTR-1um standard cellへ割り当てる
- **Semantic/physical proof**: 元のsemantic modelとmapping後の回路の対応を検査する

この処理はI²C専用ではありません。同梱例では、edge回数の計数、周期波形、serial pattern、I²C GPIO expanderを同じcompiler flowで処理しています。

## v1.2.1の対応範囲

v1.2.1はBaochip BIOの`.dis`とTR-1umを対象にしています。状態遷移が外部I/O edgeで進む小規模なリアクティブ回路を主な対象としています。

Generic compilerのwhole-equivalence proofは、到達可能なsemantic stateがおおむね`2^20 = 1,048,576`以下のB20範囲を対象とします。この値は物理FF数の上限ではありません。元プログラムのregister幅や生成回路のFF数が20 bitを超えていても、proofが扱う到達可能semantic stateがB20内なら処理対象になります。

未対応命令、I/O用途を一意に決められない設定、proof contradictionを検出した場合はbuildを停止します。

## 検証

v1.2.1のrelease regressionでは、同梱5例について設定検査、full build、mapping proof、Xschem auditを実行します。4つの単純exampleは生成済みstandard-cell netlistから出力列を計算し、期待列と比較します。I²C exampleはdirected transactionでACK、GPIO write/read、連続read、wrong-address NACKを確認し、最終mapping proofも確認します。

リポジトリ内の`.dis`から同じ回帰を実行する場合は次を使います。

```bash
make release-regression
```

BIO Cソースから`.dis`も作り直す場合は、標準WSL環境で次を実行します。

```bash
python tools/run_bio_sim_example.py ../bio-sim --all
python tools/test_example_signals.py
```

詳細な検証項目は[`docs/VERIFICATION.md`](docs/VERIFICATION.md)にあります。

## 利用方法の例

BIO上でI/O処理をCプログラムとして開発し、動作が確定した段階でbio2rtlを使って同じ外部動作を専用回路へ変換できます。BIOのプログラムを回路仕様の入力として使うため、専用回路化のために別のRTLを最初から書き直す作業を減らすことを目的としています。

## License

bio2rtlはApache License 2.0で公開しています。

bio2rtlを使用して生成したユーザー回路に、bio2rtlのApache-2.0ライセンスが自動的に適用されるわけではありません。PDKや第三者IPを使用する場合は、それぞれの利用条件を確認してください。
