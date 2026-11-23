# PowerShell APK Derleme Scripti
Write-Host "=== Maç Analiz Android APK Derleme Kontrolü ===" -ForegroundColor Cyan

# 1. Java Kontrolü
$javaVersion = java -version 2>&1 | Out-String
Write-Host "`n[1/4] Java Ortamı:" -ForegroundColor Yellow
Write-Host $javaVersion

# 2. Android SDK Kontrolü
Write-Host "`n[2/4] Android SDK Kontrolü:" -ForegroundColor Yellow
$sdkPath = $env:ANDROID_HOME
if (-not $sdkPath) { $sdkPath = $env:ANDROID_SDK_ROOT }
if (-not $sdkPath -and (Test-Path "$env:LOCALAPPDATA\Android\Sdk")) {
    $sdkPath = "$env:LOCALAPPDATA\Android\Sdk"
}

if ($sdkPath -and (Test-Path $sdkPath)) {
    Write-Host "Android SDK bulundu: $sdkPath" -ForegroundColor Green
} else {
    Write-Host "UYARI: Android SDK bulunamadı (ANDROID_HOME / ANDROID_SDK_ROOT ayarlanmamış)." -ForegroundColor Red
}

# 3. Bubblewrap Kontrolü
Write-Host "`n[3/4] Google Bubblewrap Kontrolü:" -ForegroundColor Yellow
$bw = Get-Command bubblewrap -ErrorAction SilentlyContinue
if ($bw) {
    Write-Host "Bubblewrap kurulu: $($bw.Source)" -ForegroundColor Green
} else {
    Write-Host "Bubblewrap bulunamadı. Kurmak için: npm install -g @bubblewrap/cli" -ForegroundColor DarkYellow
}

# 4. Derleme Denemesi
Write-Host "`n[4/4] APK Derleme Durumu:" -ForegroundColor Yellow
if (-not $sdkPath -or -not (Test-Path $sdkPath)) {
    Write-Host "DERLEME BAŞLATILAMADI: Sistemde Android SDK bulunmadığı için APK doğrudan derlenemiyor." -ForegroundColor Red
    Write-Host "Android TWA projesi 'android/' dizininde derlemeye hazır şekilde oluşturulmuştur." -ForegroundColor Cyan
    Write-Host "Ayrıntılı adımlar için 'android/README.md' dosyasını inceleyin." -ForegroundColor White
    exit 0
}

Write-Host "Android ortamı mevcut, derleme başlatılıyor..." -ForegroundColor Green
Set-Location -Path "$PSScriptRoot\..\android"
if (Test-Path "gradlew.bat") {
    .\gradlew.bat assembleDebug
} else {
    Write-Host "Gradlew wrapper henüz indirilmemiş. Android Studio veya 'gradle wrapper' ile başlatılabilir." -ForegroundColor Yellow
}
