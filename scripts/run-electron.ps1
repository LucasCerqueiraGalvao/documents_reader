$ErrorActionPreference = 'Stop'

$RootDir = Resolve-Path (Join-Path $PSScriptRoot '..')
$AppDir = Join-Path $RootDir 'examples\electron_app'

Set-Location $AppDir
npm install
npm start
