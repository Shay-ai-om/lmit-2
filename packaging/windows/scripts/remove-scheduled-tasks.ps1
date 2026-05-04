$ErrorActionPreference = "SilentlyContinue"

foreach ($name in @("LMIT-2 Wiki Ingest", "LMIT-2 Wiki Sync", "LMIT-2 Wiki Lint")) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false | Out-Null
}
