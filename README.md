# Nekro_plugin_mimo_tts 文本转小米tts语音插件
支持将文本通过Mimo-TTS模型转换成语音的插件

## 特色 🎇
- 可指定频道使用语音发送
- 支持自定义人设音色
- 可用官方预设音色
- 可控时长/超时时间
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

## 关于配置项 ⚙
- MiMo API Key：小米 MiMo API Key。可从 MiMo API 开放平台获取
- 启用文本转语音：总开关。开启后会向 Agent 注入语音回复工具，命令也会允许发送语音（必须打开！单启用插件不会生效！）
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

降级期间插件会暂停尝试语音 30 分钟（可通过“语音能力重试间隔”调整），避免每条消息都失败一次；`/mimo_tts_status` 可以看到当前状态。

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
