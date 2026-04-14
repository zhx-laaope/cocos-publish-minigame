# Cocos Creator 微信小游戏打包工具

一个规范化、可分发的 Cocos Creator 微信小游戏打包工具，支持多平台（macOS/Windows/Linux）。

## 目录结构

```
publish_tool/
├── README.md                      # 本文档
├── config/
│   ├── config.example.json       # 配置示例
│   └── config_schema.json        # 配置项说明
├── bin/
│   ├── publish.py                # 主入口脚本
│   └── pngquant/
│       ├── pngquant_mac          # macOS 使用的 pngquant
│       └── pngquant.exe          # Windows 使用的 pngquant
└── lib/
    ├── __init__.py
    ├── utils.py                  # 通用工具函数
    ├── upload.py                 # 阿里云 OSS 上传
    ├── texture.py                # 纹理压缩格式管理
    └── split_scripts.py          # 场景脚本拆分
```

## 快速开始

### 1. 配置

```bash
# 克隆仓库
git clone https://github.com/zhx-laaope/cocos-publish-minigame.git
cd cocos-publish-minigame

# 复制并编辑配置
cp config/config.example.json config/config.json
```

编辑 `config/config.json`，填写必要的配置项：

```json
{
    "creator": {
        "path": "/Applications/Cocos/Creator/2.4.15/CocosCreator.app/Contents/MacOS/CocosCreator"
    },
    "build": {
        "outputPath": "../build",
        "projectName": "wechatgame"
    },
    "wechat": {
        "cliPath": "/Applications/wechatwebdevtools.app/Contents/MacOS/cli"
    },
    "oss": {
        "enabled": true,
        "accessKeyId": "你的AccessKeyId",
        "accessKeySecret": "你的AccessKeySecret",
        "endpoint": "oss-cn-beijing.aliyuncs.com",
        "bucketName": "你的Bucket名称"
    },
    "feishu": {
        "appId": "飞书应用AppId",
        "appSecret": "飞书应用AppSecret",
        "webhook": "飞书机器人Webhook地址"
    }
}
```

### 2. 运行

```bash
cd bin
python publish.py -b -v 1.0.0
```

## 命令行参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `-b, --build` | 执行构建 | `-b` |
| `-v, --version` | 版本号 | `-v 1.0.0` |
| `-d, --debug` | 调试模式（不校验域名） | `-d` |
| `-c, --cdn` | 指定 CDN 地址 | `-c https://cdn.example.com` |
| `-t, --tiny` | 使用 pngquant 压缩 PNG | `-t` |
| `-T, --tiny-dirs` | 指定要压缩的目录 | `-T assets/images,assets/icons` |
| `-s, --serve` | 本地预览模式 | `-s` |
| `-u, --upload` | 上传资源到 OSS | `-u` |
| `-z, --upload_game` | 上传到微信平台 | `-z` |
| `--upload_workers` | OSS 上传并发数 | `--upload_workers 16` |
| `--config` | 指定配置文件 | `--config /path/to/config.json` |

## 完整示例

```bash
# 构建正式版本
python publish.py -b -v 1.0.0 -u -z

# 构建调试版本（不校验域名）
python publish.py -b -d -v 1.0.1

# 构建并压缩图片
python publish.py -b -t -v 1.0.2

# 本地预览
python publish.py -b -s -v 1.0.0

# 仅构建不上传
python publish.py -b -v 1.0.3
```

## 配置项详解

### creator

```json
{
    "path": "/Applications/Cocos/Creator/2.4.15/CocosCreator.app/Contents/MacOS/CocosCreator"
}
```

- `path`: Cocos Creator 可执行文件路径
  - macOS: `/Applications/Cocos/Creator/2.4.x/CocosCreator.app/Contents/MacOS/CocosCreator`
  - Windows: `C:\\Program Files\\CocosCreator\\CocosCreator.exe`

### build

```json
{
    "outputPath": "../build",
    "projectName": "wechatgame",
    "remoteDirName": "remote",
    "md5Cache": true,
    "startSceneAssetBundle": true
}
```

- `outputPath`: 构建产物输出路径（相对于项目根目录）
- `projectName`: 微信小游戏项目目录名
- `remoteDirName`: 远程资源目录名
- `md5Cache`: 是否启用 MD5 缓存
- `startSceneAssetBundle`: 是否启用 start-scene 资源包

### buildCopy

```json
{
    "enabled": true,
    "rootPath": "~/Downloads",
    "folderName": "build_copy",
    "excludes": [".git", "node_modules"]
}
```

构建前将项目复制到临时目录进行操作，避免污染原项目。

- `enabled`: 是否启用副本模式
- `rootPath`: 副本存放根目录
- `folderName`: 副本文件夹名前缀
- `excludes`: 排除的文件/目录

### pngquant

```json
{
    "enabled": false,
    "quality": "80-100",
    "binary": {
        "darwin": "bin/pngquant/pngquant_mac",
        "win32": "bin/pngquant/pngquant.exe"
    }
}
```

PNG 图片压缩配置。工具已内置 macOS 和 Windows 版本的 pngquant。

- `enabled`: 是否启用压缩
- `quality`: 压缩质量范围
- `binary`: 各平台 pngquant 路径

### ignoreResources

```json
{
    "enabled": true,
    "paths": [
        "assets/Texture/main/map_ignore",
        "assets/sceneEditor"
    ],
    "stashRoot": "temp/publish_res_ignore_stash"
}
```

打包时忽略的资源路径。脚本会自动检查并断开相关引用。

- `enabled`: 是否启用忽略
- `paths`: 要忽略的路径列表
- `stashRoot`: 忽略资源的备份目录

### textureCompression

```json
{
    "enabled": false,
    "rules": [
        {
            "name": "subpackage_compressed",
            "paths": ["assets/subpackage"],
            "formats": [
                {"name": "astc_6x6", "quality": "exhaustive"}
            ]
        }
    ]
}
```

纹理压缩格式配置，会修改 `.meta` 文件。

支持的格式：
- `astc_4x4`, `astc_5x5`, `astc_6x6`, `astc_8x8` - ASTC 格式
- `etc` - ETC 格式
- `pvrtc` - PVRTC 格式
- `png` - 保留 PNG 格式

质量级别：
- `fast` - 快速
- `normal` - 普通
- `thorough` - 彻底
- `exhaustive` - 穷举（最高质量）

### splitStartScene

```json
{
    "enabled": true
}
```

将 start-scene bundle 中的业务代码拆分到子包，减小首包体积。

### firstScreenPlugin

```json
{
    "enabled": false,
    "sourcePath": "build-temp/wechatgame"
}
```

首屏插件配置，用于替换构建产物中的文件。

### wechat

```json
{
    "cliPath": "/Applications/wechatwebdevtools.app/Contents/MacOS/cli",
    "uploadOptions": {
        "libVersion": "latest",
        "urlCheck": true,
        "minified": true,
        "es6": false,
        "enhance": false
    }
}
```

- `cliPath`: 微信开发者工具 CLI 路径
- `uploadOptions`: 上传到微信时的选项

### oss

```json
{
    "enabled": false,
    "accessKeyId": "",
    "accessKeySecret": "",
    "endpoint": "oss-cn-beijing.aliyuncs.com",
    "bucketName": "",
    "remotePath": {
        "dev": "dev/remote_assets",
        "prod": "prod/remote_assets"
    }
}
```

阿里云 OSS 配置，用于上传远程资源。

### feishu

```json
{
    "appId": "",
    "appSecret": "",
    "webhook": "",
    "qrcodeTemplateId": "",
    "qrcodeTemplateVersion": ""
}
```

飞书通知配置。构建完成后自动推送体验版二维码到飞书群。

### 环境变量

| 变量 | 说明 |
|------|------|
| `PUBLISH_CONFIG` | 指定配置文件路径 |

## 多平台支持

### macOS

直接运行即可：

```bash
python publish.py -b -v 1.0.0
```

### Windows

确保已安装 Python 3，然后运行：

```cmd
python bin\publish.py -b -v 1.0.0
```

### Linux

需要自行准备 pngquant for Linux 或安装系统包管理器版本：

```bash
# Ubuntu/Debian
sudo apt install pngquant

# 配置使用系统 pngquant
# 在 config.json 中设置:
# "pngquant": {
#     "binary": "/usr/bin/pngquant"
# }
```

## 依赖

- Python 3.6+
- Cocos Creator 2.4.x
- 微信开发者工具（用于上传）

可选依赖：
- `oss2` - 用于 OSS 上传 (`pip install oss2`)
- `requests` - 用于飞书通知 (`pip install requests`)

## 工作流程

```
1. 配置检查
   └─ 加载 config.json

2. 构建准备
   ├─ 创建项目副本（可选）
   └─ 移除忽略资源

3. 纹理压缩
   ├─ 应用压缩格式规则
   └─ 修改 .meta 文件

4. Cocos Creator 构建
   └─ 执行引擎构建

5. ��处理
   ├─ 复制构建产物
   ├─ 修改微信配置
   ├─ 压缩 JSON
   ├─ PNG 压缩（可选）
   ├─ 移除冗余资源
   ├─ 应用首屏插件（可选）
   └─ 拆分 start-scene 脚本

6. 上传（可选）
   ├─ 上传资源到 OSS
   └─ 上传到微信平台

7. 通知（可选）
   └─ 推送二维码到飞书
```

## 故障排除

### 构建失败

查看 `build_logs/build_fail_*.txt` 日志文件。

### PNG 压缩失败

确保 pngquant 二进制文件有执行权限：

```bash
chmod +x bin/pngquant/pngquant_mac
```

### OSS 上传失败

1. 检查 `oss2` 库是否安装
2. 验证 AccessKey 和 Bucket 配置
3. 检查网络连接

### 飞书通知失败

1. 确认飞书应用已开通机器人权限
2. 检查 Webhook 地址是否正确
3. 验证 AppId 和 AppSecret

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
