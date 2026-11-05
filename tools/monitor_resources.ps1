param([int]$Seconds = 1800, [string]$Out = "$env:TEMP\bnge_resource_monitor.csv")
$ErrorActionPreference = 'SilentlyContinue'
if (-not ($Append -and (Test-Path $Out))) {
  "t,elapsed_s,cpu_pct,ram_used_gb,ram_avail_gb,commit_gb,gpu_pct,gpu_mem_mb,gpu_power_w,gpu_temp_c,net_rx_mbps,net_tx_mbps,game_ws_gb,game_priv_gb,game_cpu_s,server_ws_gb,server_priv_gb,server_cpu_s,game_procs,server_procs" | Set-Content -Encoding ASCII $Out
}
$t0 = Get-Date
$prevRx = $null; $prevTx = $null; $prevAt = $null
$prevGameCpu = 0.0; $prevServerCpu = 0.0
while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
  $now = Get-Date
  $os = Get-CimInstance Win32_OperatingSystem
  $mem = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
  $cpuRow = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor | Where-Object { $_.Name -eq '_Total' } | Select-Object -First 1
  $gpuLine = (& 'C:\Windows\System32\nvidia-smi.exe' --query-gpu=utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
  $gp = @($gpuLine -split ',' | ForEach-Object { $_.Trim() })
  $net = Get-NetAdapterStatistics | Measure-Object -Property ReceivedBytes,SentBytes -Sum
  $rx = [double]$net[0].Sum; $tx = [double]$net[1].Sum
  if ($null -eq $prevRx) { $prevRx = $rx; $prevTx = $tx; $prevAt = $now }
  $dt = [math]::Max((($now - $prevAt).TotalSeconds), 0.1)
  $rxMbps = (($rx - $prevRx) * 8.0 / 1e6) / $dt
  $txMbps = (($tx - $prevTx) * 8.0 / 1e6) / $dt
  $prevRx = $rx; $prevTx = $tx; $prevAt = $now
  $games = @(Get-Process -Name 'BeamNG.drive.x64' -ErrorAction SilentlyContinue)
  $servers = @(Get-Process -Name 'BeamNG.Earth-1.8.1-beta' -ErrorAction SilentlyContinue)
  $gameCpu = ($games | Measure-Object -Property CPU -Sum).Sum; if ($null -eq $gameCpu) { $gameCpu = 0 }
  $servCpu = ($servers | Measure-Object -Property CPU -Sum).Sum; if ($null -eq $servCpu) { $servCpu = 0 }
  $gameWs = (($games | Measure-Object -Property WorkingSet64 -Sum).Sum) / 1GB
  $gamePriv = (($games | Measure-Object -Property PrivateMemorySize64 -Sum).Sum) / 1GB
  $servWs = (($servers | Measure-Object -Property WorkingSet64 -Sum).Sum) / 1GB
  $servPriv = (($servers | Measure-Object -Property PrivateMemorySize64 -Sum).Sum) / 1GB
  $totalKb = [double]$os.TotalVisibleMemorySize
  $freeKb = [double]$os.FreePhysicalMemory
  $ramUsed = ($totalKb - $freeKb) / 1048576.0
  $ramAvail = $freeKb / 1048576.0
  $commitGb = [double]$mem.CommittedBytes / 1GB
  $row = @(
    $now.ToString('HH:mm:ss'),
    [math]::Round((($now - $t0).TotalSeconds), 1),
    [int]$cpuRow.PercentProcessorTime,
    [math]::Round($ramUsed, 2), [math]::Round($ramAvail, 2), [math]::Round($commitGb, 2),
    ($(if ($gp.Count -ge 1) { $gp[0] } else { '' })),
    ($(if ($gp.Count -ge 2) { $gp[1] } else { '' })),
    ($(if ($gp.Count -ge 4) { $gp[3] } else { '' })),
    ($(if ($gp.Count -ge 5) { $gp[4] } else { '' })),
    [math]::Round($rxMbps, 2), [math]::Round($txMbps, 2),
    [math]::Round($gameWs, 2), [math]::Round($gamePriv, 2), [math]::Round($gameCpu, 1),
    [math]::Round($servWs, 2), [math]::Round($servPriv, 2), [math]::Round($servCpu, 1),
    $games.Count, $servers.Count
  ) -join ','
  Add-Content -Encoding ASCII $Out $row
  Start-Sleep -Seconds 2
}
