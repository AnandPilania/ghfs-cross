$ErrorActionPreference = 'Stop'

$packageName = 'ghfs'
$version     = '0.0.1'
$url64       = "https://github.com/anandpilania/ghfs-cross/releases/download/v$version/ghfs-windows-x86_64.exe"
$checksum64  = 'REPLACE_WITH_ACTUAL_SHA256'

$toolsDir = "$(Split-Path -parent $MyInvocation.MyCommand.Definition)"
$destPath = Join-Path $toolsDir 'ghfs.exe'

# Download the binary
Get-ChocolateyWebFile -PackageName $packageName `
                      -FileFullPath $destPath `
                      -Url64bit $url64 `
                      -Checksum64 $checksum64 `
                      -ChecksumType64 'sha256'

# Create a shim so 'ghfs' works from any terminal
Install-BinFile -Name 'ghfs' -Path $destPath
