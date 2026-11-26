# WeriAuth

Paper 1.21+ plugin. Passwords are collected through private chat input, salted, and stored as PBKDF2-HMAC-SHA256 hashes in `plugins/WeriAuth/users.db`.

- `/register` or `/reg` — begin account registration; passwords are sent as private chat input and hidden from public chat
- `/login` or `/l` — sign in the same way
- `/captcha <answer>` — solve the join captcha
- Console: `/weriauth register <player>`, `/weriauth changepassword <player>`, `/weriauth unreg <player>`

Console registration and password changes generate a one-time password and print it to the console. Share it with that player privately. Passwords are salted PBKDF2-HMAC-SHA256 hashes in `plugins/WeriAuth/users.db`.

Build with `mvn package`; install `target/weri-auth-1.0.0.jar` in the server's `plugins` folder and restart Paper. The plugin requires online-mode UUIDs for account identity; configure secure proxy UUID forwarding for proxy networks.

The jar is a compiled plugin, not encryption: Java bytecode can be inspected or decompiled. Keep bot/API keys on the bot host in `.env`; never put them in the plugin or GitHub. Publishing the Python source in a public repository makes it readable.
