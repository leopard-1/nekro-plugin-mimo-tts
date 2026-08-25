# Nekro_plugin_mimo_tts 文本转小米tts语音插件
支持将文本通过Mimo-TTS模型转换成语音的插件

## 特色 🎇
- 可指定频道使用语音发送
- 支持自定义人设音色
- 可用官方预设音色
- 可控时长/超时时间

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

6.启用插件，并打开"启用语音转文本"开关，Enjoy!!🎶

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
- 单次最多语音条数：长文本拆分后的最多语音条数，避免一次发送过多语音

------------------------------------------
### 许可证📕
该插件遵循`MIT许可证`
