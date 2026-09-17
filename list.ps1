Get-ChildItem -Path . -Recurse -File | Where-Object { $_.FullName -notmatch '\.venv' -and $_.FullName -notmatch '__pycache__' -and $_.FullName -notmatch '\.pyc' } | ForEach-Object { $_.FullName }
