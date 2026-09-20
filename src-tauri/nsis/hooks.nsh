; The PyInstaller one-file sidecar may leave a child process behind when an
; older desktop build is closed. Stop the exact backend image before NSIS
; replaces installed files during upgrade or uninstall.
!macro NSIS_HOOK_PREINSTALL
  ExecWait '"$SYSDIR\taskkill.exe" /F /T /IM "student-ai-checker-backend.exe"'
  Sleep 500
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ExecWait '"$SYSDIR\taskkill.exe" /F /T /IM "student-ai-checker-backend.exe"'
  Sleep 500
!macroend
