# 新增语言教程

## 1. 准备支持的原版

在 Windows 安装 Python 3.10+，从官方获取与 `config/versions.json` 匹配的原版，将其放到 `inputs/MobaXterm.exe`。后续命令均从仓库根目录运行。

```powershell
python tools/localize.py extract --source inputs/MobaXterm.exe --output build/source-catalog.json
```

输出目录包含所有可识别的展示属性、字符串表条目及其 `anchor`。同一英文可能出现在多个界面中，可据此判断语境。

## 2. 生成语言模板

以日语为例：

```powershell
python tools/localize.py init-language --source inputs/MobaXterm.exe --locale ja-JP --name "日本語" --output locales/ja-JP.json
```

也可以使用 `de-DE`、`fr-FR` 等标识。模板从英文资源生成，不从中文词典复制。初始译文均为空；空字符串、缺失条目会保留原英文。

## 3. 编辑 UTF-8 JSON 词典

词典结构如下。`source_sha256` 请保留模板自动生成的实际值，不要使用示例占位文字。

```json
{
  "schema_version": 1,
  "locale": "ja-JP",
  "name": "日本語",
  "source_sha256": "模板中自动填写的原版 SHA-256",
  "strings": {
    "Cancel": "キャンセル",
    "Continue": "続行",
    "Choose a smart card certificate": "スマートカード証明書を選択"
  },
  "overrides": {}
}
```

`strings` 的键必须是完整、精确的英文原文，包括空格、大小写、换行和标点。不要翻译键，只填写值。不要加入 JSON 注释或尾随逗号，也不要使用重复键。

同一个英文键会匹配多个资源位置。希望只改变某一个位置时，使用上下文覆盖。

## 4. 按上下文覆盖

从 `build/source-catalog.json` 复制真实的 `anchor`，然后在 `overrides` 中添加：

```json
{
  "DFM|TFORMCHECKCLIPBOARD|/FormCheckClipboard/sBitBtn1|Caption": {
    "source": "Continue",
    "target": "続行"
  }
}
```

这里展示的是 `overrides` 对象内部结构。覆盖项优先于 `strings`；`source` 必须与当前位置的原文一致，否则构建失败。覆盖目标为空表示该位置保留英文。字符串表也支持上下文，格式为 `STRINGTABLE|资源编号|块内索引`，以实际提取结果为准。

## 5. 保留功能相关内容

- 保留格式占位符及顺序，例如 `%s`、`%d`、`%0:x`、`%%`。
- 保留代理变量 `%host`、`%port`、`%user`、`%pass`，不要翻译其名称。
- 保留 HTML 标签（如 `<b>`、`<br>`）、URL、路径、命令、协议名和快捷键。HTML 结构和快捷键语义需人工检查，工具不保证这些语义正确。
- JSON 中用 `\r\n` 表达原文换行。使用 UTF-8 文件和有效 Unicode；不可见控制字符、丢失字符及孤立代理字符会影响构建或显示。
- `&` 常用于菜单助记键。修改助记键时，检查同一菜单是否冲突。

框架修改 `Caption`、`Hint`、`Title` 等展示属性以及 Windows 字符串表；另外允许版本清单中明确核对过的两个搜索占位框的初始 `Text`。其他输入框、下拉框 `Items.Strings`、事件处理器和连接参数不在修改范围内。

### 工具栏和主页的运行时常量

部分文字没有放在 DFM 资源中，而是 Delphi 静态 ANSI 字符串。`extract` 会同时导出经过核对的 `LITERAL|...` 上下文及 `capacity_bytes`。模板默认包含 `"runtime_encoding": null`，表示保留这些位置的英文；资源翻译仍正常生效。

要翻译这些位置，请把 `runtime_encoding` 设置为目标 Windows 非 Unicode 程序使用的代码页，例如简体中文 `cp936`、日语 `cp932`。它只是编译目标文字的编码选项，不会修改 Windows 设置；不匹配的运行环境可能显示乱码。若暂时不处理这些限制，可保持 `null`。

这类译文编码后的字节数不能超过原字符串容量。超长会明确报错，不会截断，也不会扩写到相邻指令。可以用较短的上下文译文解决，例如中文 `New tab` 的通用译文太长，而工具栏使用较短标签：

```json
{
  "LITERAL|toolbar.new_tab": {
    "source": "New tab",
    "target": "新标签"
  }
}
```

将此对象内部条目加入 `overrides`，保留其他上下文译文。没有填写的运行时条目保留英文，不回退为中文。代码页无法表示的字符也会报错。

运行时显示常量的偏移和原文记录在 `config/runtime-26.5.json`，与受支持的原版 SHA-256 绑定。不要为翻译其他语言随意添加二进制偏移；新位置需要先核对其展示用途和引用。会话树根节点及对应查找使用相同的 `User sessions` 译文，以保持名称一致。

## 6. 检查、构建和查看报告

```powershell
python tools/localize.py check --source inputs/MobaXterm.exe --locale ja-JP
python tools/localize.py build --source inputs/MobaXterm.exe --locale ja-JP --output build/ja-JP/MobaXterm_26.5_ja-JP.exe
```

也可以用 `--language C:/path/custom-language.json` 指定独立词典。输出旁会生成：

| 文件 | 内容 |
| --- | --- |
| `*.build.json` | 原版/成品/词典哈希、版本、修改统计、运行时字符串槽位及验证结果 |
| `*.translations.json` | 每处实际应用的译文及上下文 |
| `*.remaining.json` | 保留的资源文字，包含专名、协议名和内部占位文字 |

`check` 通过不等于翻译完整，也不等于界面已实测。请启动成品，检查菜单、设置、会话编辑、提示框、高 DPI、长文本和快捷键；实际连接测试应使用自己的测试环境，勿上传凭据。

## 7. 其他语言自行打包

仓库的 `package` 命令有意限制为简体中文官方发行。其他语言完成验证后，可自行创建目录，加入构建出的 EXE、与此版本匹配的原版 `CygUtils.plugin` 和 `CygUtils64.plugin`、相应语言的说明及第三方声明，再压缩：

```powershell
New-Item -ItemType Directory -Force build/ja-JP/package
Copy-Item build/ja-JP/MobaXterm_26.5_ja-JP.exe build/ja-JP/package/
Copy-Item inputs/CygUtils.plugin,inputs/CygUtils64.plugin build/ja-JP/package/
Copy-Item THIRD_PARTY_NOTICES.md build/ja-JP/package/
# 添加你自己的使用说明和测试结果后再打包
Compress-Archive -Path build/ja-JP/package/* -DestinationPath build/MobaXterm-26.5-ja-JP-test.zip
Get-FileHash build/MobaXterm-26.5-ja-JP-test.zip -Algorithm SHA256
```

此流程是贡献者的自行构建示例。打包前检查插件哈希（见 `config/versions.json`），并遵守软件本体及所用译文的授权条件。
