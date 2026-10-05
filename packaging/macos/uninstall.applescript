-- Uninstall Agent F. The macOS installer compiles this into ~/Applications/Uninstall Agent F.app.
set appDir to (POSIX path of (path to home folder)) & "Library/Application Support/agent-f/app"
set python to appDir & "/runtime/bin/python3"
set installScript to appDir & "/install/install.py"

try
	do shell script "test -x " & quoted form of python
on error
	display dialog "Agent F is already removed from this Mac." buttons {"OK"} default button "OK" with title "Uninstall Agent F"
	do shell script "rm -rf " & quoted form of POSIX path of (path to me)
	return
end try

set reply to display dialog "Remove Agent F from this Mac?" & return & return & "Your Agent F settings and logs stay unless you also delete them. The Firefox add-on stays installed either way; you can remove it from about:addons in Firefox." buttons {"Cancel", "Remove and Delete Settings", "Remove"} default button "Remove" cancel button "Cancel" with title "Uninstall Agent F" with icon caution
set extra to ""
if button returned of reply is "Remove and Delete Settings" then set extra to " --purge"
do shell script quoted form of python & " " & quoted form of installScript & " --uninstall" & extra
display dialog "Agent F is removed." buttons {"OK"} default button "OK" with title "Uninstall Agent F"
