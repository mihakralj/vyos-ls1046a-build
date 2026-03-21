param([string]$Port = "COM7", [int]$Baud = 115200)

$serial = New-Object System.IO.Ports.SerialPort $Port, $Baud, 'None', 8, 'One'
$serial.ReadTimeout  = 3000
$serial.WriteTimeout = 2000
$serial.Open()

# First: drain anything already buffered (boot messages, prompts, etc.)
Start-Sleep -Milliseconds 500
$buffered = ""
$serial.ReadTimeout = 800
try { while ($true) { $buffered += [char]$serial.ReadChar() } } catch {}

# Now send Enter and read the response
$serial.Write("`r")
Start-Sleep -Milliseconds 1200
$serial.Write("`r")
Start-Sleep -Milliseconds 1200

$out = ""
$serial.ReadTimeout = 800
try { while ($true) { $out += [char]$serial.ReadChar() } } catch {}

$serial.Close()
Write-Output "=== BUFFERED ON CONNECT ==="
Write-Output $buffered
Write-Output "=== AFTER ENTER x2 ==="
Write-Output $out
Write-Output "=== END ==="
