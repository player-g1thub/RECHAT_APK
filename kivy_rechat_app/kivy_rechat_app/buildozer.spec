
[app]
title = Realtime Chat
package.name = rechat
package.domain = org.nugie
source.dir = .
source.include_exts = py,kv,png,jpg,jpeg,txt,md,json
version = 0.1.0
requirements = python3,kivy,pillow
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,READ_EXTERNAL_STORAGE
# On Android 13+, READ_MEDIA_IMAGES is recommended. Buildozer maps this automatically for some targets.

[buildozer]
log_level = 2
warn_on_root = 1
