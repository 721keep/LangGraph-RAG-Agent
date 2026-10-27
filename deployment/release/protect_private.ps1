# Restrict only R1 release private files on NTFS; never read their contents.
$ErrorActionPreference = 'Stop'
$privatePath = Join-Path $PSScriptRoot '.private'
if (-not (Test-Path -LiteralPath $privatePath -PathType Container)) {
    throw 'Stage-three private directory does not exist.'
}
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$allowedSids = @(
    $currentSid,
    [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
    [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
)
$items = @((Get-Item -LiteralPath $privatePath)) + @(Get-ChildItem -LiteralPath $privatePath -Force)
foreach ($item in $items) {
    if ($item.PSIsContainer) {
        $acl = [System.Security.AccessControl.DirectorySecurity]::new()
        $inheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    } else {
        $acl = [System.Security.AccessControl.FileSecurity]::new()
        $inheritance = [System.Security.AccessControl.InheritanceFlags]::None
    }
    $acl.SetOwner($currentSid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in $allowedSids) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid, [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance, [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $item.FullName -AclObject $acl
}
$allowedValues = @($allowedSids | ForEach-Object { $_.Value })
foreach ($item in $items) {
    $verified = Get-Acl -LiteralPath $item.FullName
    if (-not $verified.AreAccessRulesProtected) { throw 'ACL inheritance remains enabled.' }
    foreach ($access in $verified.Access) {
        $sidValue = $access.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
        if ($sidValue -notin $allowedValues) { throw 'Unexpected private-file ACL principal.' }
    }
}
[pscustomobject]@{
    private_acl_verified = $true
    checked_items = $items.Count
    allowed_principals = @('current Windows user', 'SYSTEM', 'Administrators')
    inherited_access_removed = $true
    secret_contents_read = $false
} | ConvertTo-Json
