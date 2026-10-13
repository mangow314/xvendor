# xvendor

**在 Claude Code 裡找 codex 或 agy 要一份唯讀的第二意見，在 tmux pane 看它們做事，每份回答都留在 job 目錄。**

第二個模型的審查常抓到第一個漏掉的東西，但要拿到它很麻煩。你得手動把脈絡貼進另一個 CLI，不然就是讓 Claude 對互動式 codex 打字、再刮螢幕讀結果。而且對方用的是它自己 CLI 的權限，一次「審查」可能順手改了檔案。`xv` 把這件事變成一個指令：用各家官方 headless CLI（`codex exec`、`agy -p`）以唯讀方式執行，即時顯示對方在做什麼，並把需求、事件流和最終回答寫到磁碟，Claude（或你）可以讀，也可以在同一段對話裡追問。

![xv 流程：Claude Code 寫好 brief 並執行 xv start codex；xv 以唯讀方式跑 codex，或在唯讀 bubblewrap 掛載裡跑 agy；tmux pane 即時列出每個動作；回答寫進 job 目錄，Claude 讀 response.md](docs/assets/xv-flow.svg)

![在 tmux 裡執行 xv start codex：shell 旁開出一個 pane，列出 codex 執行的每個指令，最後印出回答與 done | 17s](docs/assets/xv.gif)

*`xv start codex --file brief.md` 印出 job id，並在呼叫端旁邊開一個 pane。pane 逐條列出 codex 執行的指令，這一輪結束時印出回答。*

這個 repo 有兩部分：

| 部分 | 內容 |
| --- | --- |
| `xv` | Python 命令列工具，可單獨在任何 shell 使用 |
| `xvendor` plugin | Claude Code skill，告訴 Claude 何時、怎麼用 `xv`：寫自足的 brief、平行跑兩家且不讓一家的答案洩漏給另一家、追問、抽查發現 |

> English: [README.md](README.md)

## 需求

- Linux。在 Arch Linux 上測試；codex job 在 macOS 上可能可用，但未測試。
- Python 3.12 以上，安裝用 [uv](https://docs.astral.sh/uv/)。
- 至少一個廠商 CLI，已安裝並登入：[codex](https://github.com/openai/codex)（`codex exec`）或 agy（Google 的 Antigravity CLI，`agy -p`）。
- 跑 agy：需要 [bubblewrap](https://github.com/containers/bubblewrap)（`bwrap`），且 agy 要先互動執行過一次，讓它的狀態目錄存在。沒有 `bwrap` 時 `xv start agy` 會拒絕執行；缺狀態目錄時，這一輪會失敗（tmux pane 會顯示缺什麼）。
- 選用：tmux。在 tmux 裡每個 job 有即時 pane；不在 tmux 裡就背景執行，用 `xv show` 讀結果。
- 用 plugin 需要 Claude Code。

## 安裝

```bash
uv tool install git+https://github.com/mangow314/xvendor
```

接著安裝 Claude Code plugin：

```bash
claude plugin marketplace add mangow314/xvendor
claude plugin install xvendor@xvendor
```

如果你開了 Claude Code 的 Bash 沙箱，`xv` 必須在沙箱外執行：它要寫 job 目錄、開 tmux pane，廠商 CLI 也要連網。在設定的 `sandbox.excludedCommands` 加上它：

```json
{ "sandbox": { "excludedCommands": ["xv *"] } }
```

skill 會要 Claude 把 `xv` 當成單獨一條指令呼叫；串接的指令（`cd … && xv …`）比對不到這筆設定，會留在沙箱裡。

## 用法

裝了 plugin 後，直接用自己的話跟 Claude 說：「叫 codex 審這份 diff」「請 agy 對這個計畫給第二意見」。在 shell 裡：

```bash
xv start codex --file brief.md     # 印出 job id，在 tmux 裡開 pane
xv start agy -m "Read src/ and list the public functions without tests"
xv wait <job>                      # 等這一輪結束；rc 0 = done
xv show <job>                      # 印出回答（response.md）
xv ask <job> -m "Explain finding 2 in more detail"   # 追問，延續同一段對話
xv stop <job>                      # 中途砍掉這一輪；session 保留
xv list                            # 所有 job 的廠商、輪次、狀態、秒數
xv close <job>                     # 關掉 job 的 pane
xv watch <job>                     # 在任何終端機跟看執行中的這一輪
```

`<job>` 可以給完整 id 或末 4 碼。每一輪有逾時（`start` 的 `--timeout`，預設 1200 秒；`ask` 沿用 job 的逾時，除非另外指定）。job 存在 `~/.local/share/xvendor/jobs/<job-id>/`（設 `XV_DATA` 可改位置，須是只含英數字、`/`、`.`、`_`、`-` 的絕對路徑），每一輪一個資料夾，內有 `request.md`、`events.jsonl`、`response.md`、`stderr.log`。

## 安全

每個 job 都是唯讀，由 `xv` 強制，不靠 brief 裡的請求：

- **codex** 用 `-s read-only` 執行，這是 codex 自己的沙箱。追問輪會再傳一次 `-c sandbox_mode="read-only"`，因為 resume 的 codex session 不會沿用第一輪的旗標。
- **agy** 跑在 bubblewrap 裡，整個檔案系統掛成唯讀。主機上只有 agy 自己的狀態目錄（`~/.gemini/antigravity-cli`，其中的設定、啟動腳本與內建 skill 仍是唯讀）可寫；另外 agy 有一個每輪專用、跑完即刪的暫存目錄。`/run/user/<uid>` 和預設的 tmux socket 目錄（`$TMUX_TMPDIR` 或 `/tmp` 下的 `tmux-<uid>`）被換成空的記憶體資料夾，agy 沒辦法透過 `systemd --user` 或你預設的 tmux server 在沙箱外執行指令。家目錄的憑證檔（`~/.ssh`、`~/.aws`、`~/.gnupg`、`~/.config/gh`、`~/.kube`、`~/.git-credentials`、`~/.npmrc`、`~/.pgpass`、`~/.netrc`、`~/.claude/.credentials.json`、`~/.codex/auth.json`）被蓋成空的。agy 自己的 `--sandbox` 擋不住它的寫檔工具，所以 xv 不靠它。agy 不會在沒有這些保護的狀態下執行：沒有 `bwrap` 時 `xv start agy` 直接拒絕，agy 的狀態目錄不完整時這一輪會失敗。

沒有涵蓋的部分：

- 不擋網路。廠商讀得到的東西都可能隨對話送出去，所以請把每個可讀的檔案都當成已分享給那家廠商。
- codex 的唯讀靠 codex 自己的沙箱；`xv` 只保證每次都帶上這個旗標。
- 用自訂 socket 的 tmux server（`tmux -S <路徑>`，不在預設目錄）不會對 agy 隱藏。
- agy 讀得到上面清單以外的所有檔案，包括 `~/.local/share/xvendor/` 裡的其他 job。skill 會要 Claude 請兩家都別讀那個資料夾，但那只是請求，擋不住。

## 費用與隱私

- 每一輪都是用你自己帳號跑的一般 `codex exec` 或 `agy -p`，計費或額度跟平常使用一樣。測試時同一份審查，agy 用的 token 約為 codex 的 2.5 倍。
- 你的 brief，以及廠商自己決定讀的每個檔案，都會送到該廠商（codex 是 OpenAI，agy 是 Google）。
- `xv` 本身不往任何地方送資料，除了你磁碟上的 job 目錄，也不保存任何資料。

## 移除

```bash
claude plugin uninstall xvendor@xvendor
claude plugin marketplace remove xvendor
uv tool uninstall xvendor
rm -rf ~/.local/share/xvendor    # job 紀錄，不要了再刪
```

## 回饋

這是我每天在用的個人專案。bug 回報、「這裡看不懂」、點子都歡迎開 [GitHub Issues](https://github.com/mangow314/xvendor/issues)。最有幫助的資訊：作業系統、廠商 CLI 版本、失敗的指令，以及該 job 的 `stderr.log`。

## 開發

```bash
git clone https://github.com/mangow314/xvendor
cd xvendor
uv sync
uv run pytest
```

測試用假的 `codex`、`agy` 腳本（`tests/fakes/`）重播錄好的事件流。需要 tmux 或 bubblewrap 的測試，在它們起不來時（例如在擋掉 unix socket 的沙箱裡）會跳過。GIF 和示意圖的製作方法：[docs/demo/](docs/demo/)。

相關專案：mango-mods 裡的 [xv-band](https://github.com/mangow314/mango-mods/tree/master/xv-band) 會在 Claude Code 提示框上方列出執行中的 xv job。

## 授權

[MIT](LICENSE)
