#Requires -Version 5.1
<#
.SYNOPSIS
    Build an installer for EmberOS-Windows.
    Tries NSIS first (EXE), then WiX (MSI), then prints instructions.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$installerDir = "$ROOT\installer"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  EmberOS-Windows Installer Builder" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# ── Option A: NSIS ───────────────────────────────────────────────
$nsisExe = $null
$nsisLocations = @(
    (Get-Command makensis -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }),
    "C:\Program Files (x86)\NSIS\makensis.exe",
    "C:\Program Files\NSIS\makensis.exe"
)
foreach ($loc in $nsisLocations) {
    if ($loc -and (Test-Path $loc)) {
        $nsisExe = $loc
        break
    }
}

if ($nsisExe) {
    Write-Host "Found NSIS at: $nsisExe" -ForegroundColor Green
    Write-Host "Building EXE installer..."

    # Generate NSIS script
    $nsiContent = @"
!include "MUI2.nsh"

Name "EmberOS-Windows"
OutFile "EmberOS-Windows-Setup.exe"
InstallDir "`$PROGRAMFILES\EmberOS"
RequestExecutionLevel admin

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Install"
    SetOutPath `$INSTDIR

    ; Python source package
    SetOutPath "`$INSTDIR\emberos"
    File /r "$ROOT\emberos\*.*"

    SetOutPath "`$INSTDIR\config"
    File /r "$ROOT\config\*.*"

    SetOutPath "`$INSTDIR\assets"
    File /nonfatal /r "$ROOT\assets\*.*"

    SetOutPath "`$INSTDIR\scripts"
    File /r "$ROOT\scripts\*.*"

    SetOutPath `$INSTDIR
    File "$ROOT\setup.ps1"
    File "$ROOT\launch.ps1"
    File "$ROOT\emberos.bat"

    ; Create Start Menu shortcut
    CreateDirectory "`$SMPROGRAMS\EmberOS"
    CreateShortCut "`$SMPROGRAMS\EmberOS\EmberOS Launch.lnk" "powershell.exe" "-ExecutionPolicy Bypass -File `"`$INSTDIR\launch.ps1`"" "`$INSTDIR\assets\icon.ico"
    CreateShortCut "`$SMPROGRAMS\EmberOS\EmberOS Setup.lnk" "powershell.exe" "-ExecutionPolicy Bypass -File `"`$INSTDIR\setup.ps1`"" "" "" "" "" "Run first-time setup"

    ; Create uninstaller
    WriteUninstaller "`$INSTDIR\Uninstall.exe"
    CreateShortCut "`$SMPROGRAMS\EmberOS\Uninstall.lnk" "`$INSTDIR\Uninstall.exe"
SectionEnd

Section "Post-Install" SEC_POST
    ; Run setup.ps1
    DetailPrint "Running first-time setup (this may take a while)..."
    nsExec::ExecToLog 'powershell.exe -ExecutionPolicy Bypass -File "`$INSTDIR\setup.ps1"'
SectionEnd

Section "Uninstall"
    ; Stop service
    nsExec::Exec 'sc.exe stop EmberOSAgent'
    nsExec::Exec 'sc.exe delete EmberOSAgent'

    ; Remove files
    RMDir /r "`$INSTDIR\emberos"
    RMDir /r "`$INSTDIR\config"
    RMDir /r "`$INSTDIR\assets"
    RMDir /r "`$INSTDIR\scripts"
    RMDir /r "`$INSTDIR\env"
    RMDir /r "`$INSTDIR\bitnet"
    RMDir /r "`$INSTDIR\models"
    RMDir /r "`$INSTDIR\logs"
    RMDir /r "`$INSTDIR\data"
    Delete "`$INSTDIR\setup.ps1"
    Delete "`$INSTDIR\launch.ps1"
    Delete "`$INSTDIR\emberos.bat"
    Delete "`$INSTDIR\Uninstall.exe"
    RMDir "`$INSTDIR"

    ; Remove shortcuts
    RMDir /r "`$SMPROGRAMS\EmberOS"
SectionEnd
"@

    $nsiPath = "$installerDir\emberos.nsi"
    Set-Content -Path $nsiPath -Value $nsiContent -Encoding UTF8
    Write-Host "  Generated $nsiPath"

    # Compile
    Push-Location $installerDir
    try {
        & $nsisExe "emberos.nsi"
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "SUCCESS: Installer built at $installerDir\EmberOS-Windows-Setup.exe" -ForegroundColor Green
        } else {
            Write-Host "NSIS compilation failed with exit code $LASTEXITCODE" -ForegroundColor Red
        }
    } finally {
        Pop-Location
    }
    exit 0
}

# ── Option B: WiX ────────────────────────────────────────────────
$wixBin = $null
$wixLocations = @(
    "C:\Program Files (x86)\WiX Toolset v3.14\bin",
    "C:\Program Files (x86)\WiX Toolset v3.11\bin",
    "C:\Program Files (x86)\WiX Toolset v4.0\bin"
)
foreach ($loc in $wixLocations) {
    if (Test-Path "$loc\candle.exe") {
        $wixBin = $loc
        break
    }
}

if ($wixBin) {
    Write-Host "Found WiX at: $wixBin" -ForegroundColor Green
    Write-Host "Building MSI installer..."

    $wxsPath = "$installerDir\emberos.wxs"

    $wxsContent = @"
<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Id="*" Name="EmberOS-Windows" Language="1033" Version="1.0.0.0"
           Manufacturer="EmberOS" UpgradeCode="A1B2C3D4-E5F6-7890-ABCD-EF1234567890">
    <Package InstallerVersion="200" Compressed="yes" InstallScope="perMachine" />
    <MajorUpgrade DowngradeErrorMessage="A newer version is already installed." />
    <MediaTemplate EmbedCab="yes" />

    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="ProgramFilesFolder">
        <Directory Id="INSTALLFOLDER" Name="EmberOS">
          <Directory Id="DIR_emberos" Name="emberos" />
          <Directory Id="DIR_config" Name="config" />
          <Directory Id="DIR_assets" Name="assets" />
          <Directory Id="DIR_scripts" Name="scripts" />
        </Directory>
      </Directory>
      <Directory Id="ProgramMenuFolder">
        <Directory Id="AppMenuFolder" Name="EmberOS" />
      </Directory>
    </Directory>

    <ComponentGroup Id="CoreFiles" Directory="INSTALLFOLDER">
      <Component Id="SetupScript" Guid="*">
        <File Source="$ROOT\setup.ps1" />
      </Component>
      <Component Id="LaunchScript" Guid="*">
        <File Source="$ROOT\launch.ps1" />
      </Component>
      <Component Id="EmberBat" Guid="*">
        <File Source="$ROOT\emberos.bat" />
      </Component>
    </ComponentGroup>

    <Feature Id="MainFeature" Title="EmberOS" Level="1">
      <ComponentGroupRef Id="CoreFiles" />
    </Feature>

    <CustomAction Id="RunSetup" Directory="INSTALLFOLDER"
                  ExeCommand='powershell.exe -ExecutionPolicy Bypass -File "[INSTALLFOLDER]setup.ps1"'
                  Execute="deferred" Return="asyncNoWait" Impersonate="no" />

    <InstallExecuteSequence>
      <Custom Action="RunSetup" After="InstallFiles">NOT Installed</Custom>
    </InstallExecuteSequence>
  </Product>
</Wix>
"@

    Set-Content -Path $wxsPath -Value $wxsContent -Encoding UTF8
    Write-Host "  Generated $wxsPath"

    Push-Location $installerDir
    try {
        & "$wixBin\candle.exe" "emberos.wxs"
        if ($LASTEXITCODE -eq 0) {
            & "$wixBin\light.exe" "emberos.wixobj" -o "EmberOS-Windows-Setup.msi"
            if ($LASTEXITCODE -eq 0) {
                Write-Host ""
                Write-Host "SUCCESS: Installer built at $installerDir\EmberOS-Windows-Setup.msi" -ForegroundColor Green
            } else {
                Write-Host "WiX light.exe failed" -ForegroundColor Red
            }
        } else {
            Write-Host "WiX candle.exe failed" -ForegroundColor Red
        }
    } finally {
        Pop-Location
    }
    exit 0
}

# ── Neither found ────────────────────────────────────────────────
Write-Host ""
Write-Host "No installer tool found (NSIS or WiX)." -ForegroundColor Yellow
Write-Host ""
Write-Host "To build an installer, install one of:" -ForegroundColor White
Write-Host "  Option A (recommended): NSIS — free, lightweight"
Write-Host "    Download: https://nsis.sourceforge.io/Download"
Write-Host "    After install, re-run this script."
Write-Host ""
Write-Host "  Option B: WiX Toolset v3 — MSI installer"
Write-Host "    Download: https://wixtoolset.org/releases/"
Write-Host ""
exit 1
