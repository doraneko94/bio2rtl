# bio2rtl 標準環境

bio2rtlでは、ISHI会[`OpenEDA-PDK_SetupScript`](https://github.com/ishi-kai/OpenEDA-PDK_SetupScript)が配布する**OpenSUSI-TR10用WSLイメージ**を標準環境とします。

## Windows側

公式READMEの **“Image for WSL version of OpenSUSI-TR10 (Tokai Rika)”** を取得・展開し、PowerShellから登録します。公式README掲載例：

```powershell
wsl --import-in-place ubuntu2204_ishi-kai_EDA .\ubuntu2204_ishi-kai_EDA\ext4.vhdx
wsl -d ubuntu2204_ishi-kai_EDA
```

登録名を変更した場合は、以後その名前を使用してください。

## WSL側

bio2rtlリポジトリのルートで、初回に次を実行します。

```bash
bash tools/setup_bio2rtl_env.sh
source .venv/bin/activate
python tools/run_bio_sim_example.py ../bio-sim set_after_3_rises
```

デモrunnerは`bin/bio2rtl`の実行権限には依存せず、現在のvirtual environmentから`python -m bio2rtl`を呼び出します。WindowsでZIPを展開して実行ビットが失われた場合でも、この経路は動作します。

`setup_bio2rtl_env.sh`は、bio2rtlを継続して使用するための標準環境を準備します。Quick demo専用ではありません。

既存の`.venv`がWindows用（`Scripts/python.exe`）などで、WSL/Linux用の`.venv/bin/python`を持たない場合は、その`.venv`を削除してWSL/Linux用に再作成します。

- `binutils-riscv64-linux-gnu` (`riscv64-linux-gnu-objdump`)
- Python virtual environment
- `ziglang`
- Baochip公式`bio-sim`（既に`../bio-sim`があれば再利用）

TR-1um PDKとEDAツールはOpenSUSI-TR10用イメージ側を使用します。`bio2rtl check/build`自体はPDKを直接参照しませんが、Xschem/ngspice、DRC/LVSまで同じ環境で続けられます。

PDKの探索に`/root`固定パスは使いません。標準では`$HOME/pdk/TR-1um`を使用するため、通常ユーザーで実行する場合はそのユーザーのhome以下にPDKがあればそのまま利用できます。別の場所にある場合は`PDK_ROOT`と`PDK`を指定します。`PDK_ROOT`は通常`TR-1um`を含む親directoryですが、`TR-1um` directoryそのものを指定する形式にも対応しています。

```bash
export PDK_ROOT="$HOME/pdk"
export PDK="TR-1um"
```

## `no RISC-V objdump found` が出る場合

```bash
sudo apt update
sudo apt install -y binutils-riscv64-linux-gnu
riscv64-linux-gnu-objdump --version
```

bio-simは`.dis`生成にRISC-V `objdump`を使用します。Windowsネイティブで別のbinutilsを用意するより、このWSL環境内で揃える方法を標準とします。

## Windows上の既存リポジトリを使う場合

たとえばWindowsの`C:\Python\bio2rtl`はWSLから通常、次で参照できます。

```bash
cd /mnt/c/Python/bio2rtl
```

ただし、速度とLinux toolchainとの相性を優先する場合は、bio2rtlとbio-simをWSL側のhome directoryへ置くことを推奨します。
