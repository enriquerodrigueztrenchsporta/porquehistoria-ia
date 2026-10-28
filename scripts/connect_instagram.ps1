[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    [string]$Repository,
    [ValidatePattern('^[A-Za-z0-9_.]+$')]
    [string]$ExpectedUsername = 'porquehistoria',
    [ValidatePattern('^v[0-9]+\.0$')]
    [string]$ApiVersion = 'v25.0'
)

# Run interactively. Never supply the token as an argument or paste it in chat.
$ErrorActionPreference = 'Stop'
Set-PSDebug -Off

function Invoke-MetaRead {
    param([string]$Endpoint, [string]$Token, [hashtable]$Fields)
    $parts = @()
    foreach ($key in $Fields.Keys) {
        $parts += [Uri]::EscapeDataString([string]$key) + '=' + [Uri]::EscapeDataString([string]$Fields[$key])
    }
    $uri = 'https://graph.facebook.com/' + $ApiVersion + '/' + $Endpoint + '?' + ($parts -join '&')
    try {
        Invoke-RestMethod -Method Get -Uri $uri -Headers @{ Authorization = 'Bearer ' + $Token } -TimeoutSec 60 -MaximumRedirection 0
    }
    catch {
        # Do not display exception objects: HTTP diagnostics can contain secrets.
        throw 'Meta rechazo la comprobacion. Revisa vigencia del token, permisos, aplicacion y vinculacion de la Pagina.'
    }
}

function Set-PrivateRepositorySecret {
    param([string]$Name, [string]$Value)
    # stdin keeps credentials out of command-line arguments and process listings.
    $Value | & $script:ghExecutable secret set $Name --repo $Repository
    if ($LASTEXITCODE -ne 0) {
        throw ('No se pudo guardar el secreto ' + $Name + ' en GitHub. Comprueba el acceso al repositorio.')
    }
}

function Set-RepositoryVariable {
    param([string]$Name, [string]$Value)
    & $script:ghExecutable variable set $Name --repo $Repository --body $Value
    if ($LASTEXITCODE -ne 0) {
        throw ('No se pudo guardar la variable ' + $Name + ' en GitHub.')
    }
}

$script:ghExecutable = (Get-Command gh -ErrorAction Stop).Source
& $script:ghExecutable repo view $Repository --json nameWithOwner --jq .nameWithOwner
if ($LASTEXITCODE -ne 0) {
    throw 'El repositorio no existe o gh no tiene acceso. Configura GitHub antes de conectar Instagram.'
}

Write-Host ('Se conectara exclusivamente @' + $ExpectedUsername + ' con ' + $Repository + '.')
Write-Host 'Necesitas un token de USUARIO de Facebook de larga duracion, con los permisos del documento docs/instagram.md.'
Write-Host 'El asistente descubre la Pagina, obtiene su token, verifica Instagram y guarda los secretos cifrados de GitHub.'
Write-Host 'No se publicara ningun video ni se activara la programacion en este paso.'

$secureToken = $null
$userToken = $null
$pageToken = $null
$pages = $null
$selectedPage = $null
try {
    $secureToken = Read-Host 'Pega el token de usuario de larga duracion (entrada oculta)' -AsSecureString
    $userToken = [System.Net.NetworkCredential]::new('', $secureToken).Password.Trim()
    if ([string]::IsNullOrWhiteSpace($userToken) -or $userToken.Contains("`r") -or $userToken.Contains("`n")) {
        throw 'El token esta vacio o tiene saltos de linea.'
    }

    # Discover only Pages that the user has authorized. Do not print their tokens.
    $after = $null
    $found = @()
    for ($pageNumber = 0; $pageNumber -lt 20; $pageNumber++) {
        $fields = @{ fields = 'id,name,access_token,instagram_business_account{id,username}'; limit = '100' }
        if ($after) { $fields['after'] = $after }
        $pages = Invoke-MetaRead -Endpoint 'me/accounts' -Token $userToken -Fields $fields
        foreach ($page in @($pages.data)) {
            if ($page.instagram_business_account.username -ieq $ExpectedUsername) {
                $found += $page
            }
        }
        if (-not $pages.paging.next) { break }
        # Use only the cursor, not Meta's next URL (which may contain a token).
        $after = $pages.paging.cursors.after
        if (-not $after) { throw 'No se pudo continuar la lista de Paginas de Meta.' }
        if ($pageNumber -eq 19) { throw 'Demasiadas Paginas para este asistente; limita el acceso del token a PorQueHistoria.' }
    }
    if ($found.Count -ne 1) {
        throw ('No se encontro una unica Pagina autorizada vinculada a @' + $ExpectedUsername + '. Revisa la vinculacion y los permisos del token.')
    }
    $selectedPage = $found[0]
    $instagramId = [string]$selectedPage.instagram_business_account.id
    $pageToken = [string]$selectedPage.access_token
    if ($instagramId -notmatch '^[0-9]+$' -or [string]::IsNullOrWhiteSpace($pageToken)) {
        throw 'Meta no proporciono identificador de Instagram o token de Pagina. No se guardo la conexion.'
    }
    $account = Invoke-MetaRead -Endpoint $instagramId -Token $pageToken -Fields @{ fields = 'id,username' }
    if ([string]$account.id -ne $instagramId -or $account.username -ine $ExpectedUsername) {
        throw 'La comprobacion de la cuenta no coincide con el destino esperado. No se guardo la conexion.'
    }
    $limits = Invoke-MetaRead -Endpoint ($instagramId + '/content_publishing_limit') -Token $pageToken -Fields @{ fields = 'config,quota_usage' }
    if ($null -eq $limits.data) {
        throw 'Meta no confirmo acceso a la publicacion de contenido. No se guardo la conexion.'
    }

    # Store the ID before its token so interrupted setup cannot enable a partial pair.
    # Publishing remains disabled until the separate workflow activation step.
    Set-PrivateRepositorySecret -Name 'INSTAGRAM_USER_ID' -Value $instagramId
    Set-PrivateRepositorySecret -Name 'INSTAGRAM_ACCESS_TOKEN' -Value $pageToken
    Set-RepositoryVariable -Name 'IG_LOGIN_MODE' -Value 'facebook'
    Set-RepositoryVariable -Name 'IG_EXPECTED_USERNAME' -Value $ExpectedUsername
    Set-RepositoryVariable -Name 'IG_API_VERSION' -Value $ApiVersion
    Write-Host ('Conexion verificada y guardada para @' + $ExpectedUsername + '. No se ha publicado contenido.')
    Write-Host 'Los tokens pueden ser revocados por Meta. Conserva acceso a la cuenta para reconectarla si ocurre.'
}
finally {
    $userToken = $null
    $pageToken = $null
    $pages = $null
    $selectedPage = $null
    $found = $null
    $page = $null
    if ($secureToken) { $secureToken.Dispose() }
    $secureToken = $null
}
