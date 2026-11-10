Get-CimInstance Win32_Process -Filter "Name='vivado.exe'" | Where-Object { $_.CommandLine -like '*chipdesign_nocpu_h9ulr062*' } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress
