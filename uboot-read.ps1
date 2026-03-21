param(
    [string]$Port = "COM7",
    [int]$Baud = 115200,
    [string]$Command = "",
    [int]$WaitMs = 3000
)

$serial = New-Object System.IO.Ports.SerialPort $Port, $Baud, 'None', 8, 'One'
$serial.ReadTimeout  = 3000
$serial.WriteTimeout = 2000
$serial.Open()

# Flush RX buffer
Start-Sleep -Milliseconds 500
if ($serial.BytesToRead -gt 0) { $serial.ReadExisting() | Out-Null }

# Send Enter to get a prompt
$serial.Write("`r")
Start-Sleep -Milliseconds 800

# Send command if provided
if ($Command -ne "") {
    $serial.Write($Command + "`r")
    Start-Sleep -Milliseconds $WaitMs
}

# Drain all output
$out = ""
$serial.ReadTimeout = 800
try { while ($true) { $out += [char]$serial.ReadChar() } } catch {}

$serial.Close()
Write-Output $out
Write-Output "=== AFTER ENTER x2 ==="
Write-Output $out
Write-Output "=== END ==="
