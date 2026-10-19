# Browser extension (optional)

Fills the post box on a LinkedIn or X tab with a post you already approved. It never clicks Post.

1. Run the local API: `python run_api.py`
2. In the dashboard: approve a post, then click **Prepare to publish**.
3. Chrome -> `chrome://extensions` -> turn on Developer mode -> **Load unpacked** -> pick this folder.
4. Open the LinkedIn or X composer, click the extension icon, choose the post, click **Fill**.

Limits (be aware):
- Site layouts change often. The selectors in `popup.js` (`fillComposer`) may stop working. The clipboard
  workflow in the dashboard always works.
- X threads: only post 1 is filled.
- Images cannot be attached by the extension. Attach the file yourself.
- It only talks to `http://127.0.0.1:8000`. It needs only `activeTab` and `scripting`.
- This extension was syntax-checked but could not be tested against the live sites in the build environment.
