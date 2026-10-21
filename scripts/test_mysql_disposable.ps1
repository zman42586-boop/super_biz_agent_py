param(
    [string]$ContainerName = 'superbiz-mysql',
    [string]$DockerEndpoint = 'npipe:////./pipe/docker_engine'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$testDbName = 'codex_aiops_test_' + [Guid]::NewGuid().ToString('N').Substring(0,16)
$testDbUser = 'codex_test_' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$testDbPassword = [Guid]::NewGuid().ToString('N')
$previousTestUrl = $env:HARNESS_TEST_MYSQL_URL
if ($testDbName -notmatch '^codex_aiops_test_[0-9a-f]{16}$' -or $testDbUser -notmatch '^codex_test_[0-9a-f]{8}$') {
    throw 'Invalid generated temporary database or user name'
}
$testExitCode = 1
Push-Location -LiteralPath $projectRoot
try {
    $createSql = "CREATE DATABASE ``$testDbName`` CHARACTER SET utf8mb4; CREATE USER '$testDbUser'@'%' IDENTIFIED BY '$testDbPassword'; GRANT ALL ON ``$testDbName``.* TO '$testDbUser'@'%';"
    docker -H $DockerEndpoint exec -e "CODEX_SQL=$createSql" $ContainerName sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -e "$CODEX_SQL"'
    if ($LASTEXITCODE -ne 0) { throw 'Temporary database setup failed' }
    $env:HARNESS_TEST_MYSQL_URL = "mysql+pymysql://${testDbUser}:${testDbPassword}@127.0.0.1:3306/${testDbName}?charset=utf8mb4"
    & .\.venv\Scripts\python.exe -m pytest -o addopts= --no-cov -q -p no:cacheprovider tests/test_mysql_worker_recovery.py
    $testExitCode = $LASTEXITCODE
} finally {
    $env:HARNESS_TEST_MYSQL_URL = $previousTestUrl
    if ($testDbName -notmatch '^codex_aiops_test_[0-9a-f]{16}$' -or $testDbUser -notmatch '^codex_test_[0-9a-f]{8}$') {
        throw 'Refusing cleanup outside the generated temporary database namespace'
    }
    $cleanupSql = "DROP DATABASE IF EXISTS ``$testDbName``; DROP USER IF EXISTS '$testDbUser'@'%';"
    docker -H $DockerEndpoint exec -e "CODEX_SQL=$cleanupSql" $ContainerName sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -e "$CODEX_SQL"'
    if ($LASTEXITCODE -ne 0) { throw "Cleanup failed; inspect temporary database $testDbName" }
    Pop-Location
}
if ($testExitCode -ne 0) { throw "MySQL integration tests failed: $testExitCode" }
Write-Output 'Disposable MySQL integration tests passed; temporary database and user removed.'
