# Nekro_plugin_mimo_tts 文本转小米tts语音插件
支持将文本通过Mimo-TTS模型转换成语音的插件

## 特色 🎇
- 可指定频道使用语音发送
- 支持自定义人设音色
- 可用官方预设音色
- 可控时长/超时时间
- 可切换文字+语音同步发送，支持用户临时说“打字/用文字”切换
- 链接不会被朗读，会自动作为单独文字消息发送并提示用户
- NapCat 语音转换失败时自动降级为音频文件，不再直接丢消息

## 开始使用 ✍
1.在`插件市场`中找到该插件并下载

2.注册或登录一个小米 Mimo账户，链接 https://mimo.mi.com/

3.在API KEY栏目创建一个API Key并填入配置项的`MiMo API Key`中

4.滑动到下方的`选择模型`，这里需要注意下：
- 如果“可定制的人设内容`CUSTOM_PERSONA`”为空，则使用`MiMo-v2.5-tts`并在“官方预设音色`PRESET_VOICE`”选择一个官方预设音色
     - 可用的官方预设有：mimo_default、冰糖、茉莉、苏打、白桦、Mia、Chloe、Milo、Dean
     - （仅在“可定制的人设内容`CUSTOM_PERSONA`”为空时生效）
- 反之则使用`mimo-v2.5-tts-voicedesign`并填写自己定制好的人设内容，
     - 详细写法可见[Mimo官方文档](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/speech-synthesis-v2.5)

5.在“支持的频道`SUPPORTED_CHANNELS`”填入`chat_key`，你可以在“频道管理”处获取
- 有两种写法：
  - 对于群聊：可用`onebot_v11_group_123456789`或`onebot_group_123456789`
  - 对于私聊：可用`onebot_v11_private_123456789`或`onebot_private_123456789`

6.启用插件，并打开“启用文本转语音”开关，Enjoy!!🎶

7.如果希望每次回复都同时发送文字和语音，打开“文字与语音同步发送”开关；也可以在聊天里说“打字”临时切换。

## 关于配置项 ⚙
- MiMo API Key：小米 MiMo API Key。可从 MiMo API 开放平台获取
- 启用文本转语音：总开关。开启后会向 Agent 注入语音回复工具，命令也会允许发送语音（必须打开！单启用插件不会生效！）
- 文字与语音同步发送：打开后自动回复会同时发送文字和语音；关闭时默认仅语音，用户说“打字/用文字”等会临时开启同步
- 临时同步发送轮数：用户临时要求同步后生效的对话轮数，默认 5 轮；用户明确说“接下来N次”时以用户说明为准
- 支持的频道：填写`chat_key`，否则插件不会被触发
- 可定制的人设内容：用于 MiMo voice design 的音色/角色/语气描述。非空时优先于官方预设音色，并自动使用 voice design 模型
- 官方预设音色：当“可定制的人设内容”为空时生效。支持 mimo_default、冰糖、茉莉、苏打、白桦、Mia、Chloe、Milo、Dean
- 选择流式输出：开启后使用 MiMo SSE 流式接口并拼接 PCM16 音频；关闭后使用非流式 WAV 输出
- 语音合成超时时间：单段语音合成超时时间，单位秒
- 选择模型：支持 mimo-v2.5-tts 和 mimo-v2.5-tts-voicedesign。填写自定义人设时会优先使用 voice design
- 最长语音消息时长：单条语音最长时长，单位秒。文本较长时会按估算拆成多条语音
- MiMo API Base URL：默认为`https://api.xiaomimimo.com/v1`，非必要请不要更改，除非你知道你在做什么
- 失败时回退文本：合成或发送语音失败时，是否回退发送原始文本
- 语音发送失败时改发音频文件：NapCat 缺少 ntsilk 支持导致语音转换失败时，自动改为发送 WAV 音频文件（默认开启）
- 语音能力重试间隔：语音转换失败后暂停尝试语音的秒数，到期后自动重试（默认 1800 秒）
- 单次最多语音条数：长文本拆分后的最多语音条数，避免一次发送过多语音

------------------------------------------
## 文字与语音发送模式 💬

从 `v0.3.0` 起支持三种发送方式：

| 模式 | 说明 |
| --- | --- |
| 仅语音 | 默认模式，只发送语音消息 |
| 文字+语音同步 | 同一条回复同时发送文字和语音，避免用户看不到文字 |
| 仅文字 | 用户明确要求“不要语音”时只发送文字 |

### 配置开关

- **文字与语音同步发送**：打开后所有自动回复都会同时发送文字和语音。
- **临时同步发送轮数**：默认 5 轮。用户说“打字/用文字”等提示后，接下来 5 轮对话自动同步；用户说“接下来 10 次打字”时以用户说明为准。

### 用户临时说明

用户直接在聊天里说下面这些话即可，**用户说明优先于配置开关，且最新说明优先**：

| 用户说法 | 效果 |
| --- | --- |
| `打字` / `用文字` / `文字和语音` | 接下来 N 轮对话同时发文字和语音 |
| `以后都打字` / `每次都发文字` | 持续同步，直到修改或重启插件 |
| `接下来10次打字` | 同步 10 轮对话 |
| `不要语音` / `只发文字` | 接下来 N 轮对话只发文字 |
| `不要文字` / `只要语音` | 接下来 N 轮对话只发语音 |
| `恢复默认` / `自动模式` | 清除临时设置，重新跟随配置开关 |

### 命令

```text
/mimo_tts_text on      # 文字+语音同步（持续）
/mimo_tts_text off     # 仅语音（持续）
/mimo_tts_text text    # 仅文字（持续）
/mimo_tts_text auto    # 跟随配置开关
/mimo_tts_text          # 查看当前模式
```

命令设置会持续生效，优先级高于配置开关；用户随后发送“打字/不要语音”等临时说明时，以最新的临时说明为准。`/mimo_tts` 命令本身是显式请求语音，不受同步开关影响。`/mimo_tts_status` 可以查看当前的文字发送模式。

------------------------------------------
## 链接处理 🔗

从 `v0.4.0` 起，语音合成前会自动识别文本中的链接：

- 支持 `https://`、`http://`、`ftp://`、`www.` 开头的链接，以及 Markdown 链接 `[文字](https://...)`。
- 链接不会送入 TTS 朗读，而是在语音之前**单独发送一条文字消息**：
  ```text
  链接无法朗读，已单独发送：
  https://example.com
  ```
- 朗读文本中会保留一个“（链接已单独发送）”提示；多个链接会合并成一条消息并去重。
- 仅文字模式保持原样，链接直接留在文字里，不会拆分。
- 如果链接消息发送失败，朗读文本会提示“（链接发送失败）”，并在日志中记录错误。

------------------------------------------
## 语音发送失败？⚠

如果日志里出现下面这类错误：

```text
Requested output format 'ntsilk_s16le' is not a suitable output format
Command failed: /usr/bin/ffmpeg ... -c:a ntsilk_s16le -f ntsilk_s16le ...
```

说明 **NapCat 使用的 ffmpeg 不带腾讯 SILK 编码器**，与 MiMo API 无关。常见于 NapCat 4.18.36+ 的部分 Docker 镜像（尤其是 ARM64），此时 NapCat 没有可用的 Native FFmpeg Addon，会退回系统 `/usr/bin/ffmpeg`。

从 `v0.2.0` 起，插件会自动降级，尽量不丢消息：

1. 先正常尝试发送语音；
2. 若 NapCat 报 `ntsilk`/silk 转换错误，自动改为发送 WAV 音频文件；
3. 若音频文件也发送失败，才按“失败时回退文本”发送原始文本。

降级期间插件会暂停尝试语音 30 分钟（可通过“语音能力重试间隔”调整），避免每条消息都失败一次；`/mimo_tts_status` 可以看到当前状态。降级发送的是 WAV 音频文件，体积约为 2.8 MB/分钟，长文本时请留意群文件大小限制。

### 恢复真正的语音消息

在 **NapCat 容器内**以 root 执行：

```bash
# 仅检查
sh scripts/fix-napcat-ntsilk.sh --check

# 安装/修复（会下载支持 ntsilk 的 ffmpeg，并保留系统原文件）
sh scripts/fix-napcat-ntsilk.sh
```

如果容器里没有这个脚本，可以直接下载：

```bash
curl -fsSL https://raw.githubusercontent.com/leopard-1/nekro-plugin-mimo-tts/main/scripts/fix-napcat-ntsilk.sh -o /tmp/fix-napcat-ntsilk.sh
sh /tmp/fix-napcat-ntsilk.sh
```

脚本会：

- 下载带 `ntsilk_s16le` 的 ffmpeg 到 `/usr/local/bin/ffmpeg-ntsilk`；
- 把 `/usr/bin/ffmpeg`、`/usr/bin/ffprobe` 替换为很小的分发脚本，原文件保留为 `*.real`；
- 为 ffprobe 增加 silk 文件时长兜底（NapCat 发送语音后会读取 silk 时长）。

执行后重启 NapCat 容器即可。若你的架构没有对应二进制，或不想改系统文件，也可以改用自带 Native FFmpeg Addon 的 NapCat 版本/镜像。

参考：<https://github.com/NapNeko/NapCatQQ/issues/2134>

------------------------------------------
### 许可证📕
该插件遵循`MIT许可证`
