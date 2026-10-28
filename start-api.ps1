# OntoPlatform 启动脚本（API + Web 控制台）
#
#   .\start-api.ps1              # 默认 127.0.0.1:8100，库 .onto/platform.db
#   .\start-api.ps1 -Port 8101   # 换端口
#   .\start-api.ps1 -Host 0.0.0.0
#
# LLM 走 OpenAI 兼容协议，默认指向本机 llmrouter（http://127.0.0.1:8000/v1）。
# 已有同名环境变量时以环境变量为准（可用来换成其它模型服务）。

param(
    [string]$BindHost = '127.0.0.1',
    [int]$Port = 8100,
    [string]$Db = '.onto/platform.db'
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not $env:ONTO_LLM_BASE_URL) { $env:ONTO_LLM_BASE_URL = 'http://127.0.0.1:8000/v1' }
if (-not $env:ONTO_LLM_MODEL)    { $env:ONTO_LLM_MODEL    = 'deepseek-v4-flash' }
if (-not $env:ONTO_LLM_API_KEY)  { $env:ONTO_LLM_API_KEY  = 'sk-123' }

Write-Host "onto-api  http://${BindHost}:$Port   db=$Db"
Write-Host "llm       $($env:ONTO_LLM_MODEL) @ $($env:ONTO_LLM_BASE_URL)"
onto-api --db $Db --host $BindHost --port $Port
