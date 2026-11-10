Get-CimInstance Win32_Process -Filter "Name='vivado.exe'" | Where-Object { $_.CommandLine -like '*chipdesign_nocpu_zasr9zas*' } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress
