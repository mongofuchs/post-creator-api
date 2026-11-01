# PostCreator

The Android app needs an internet-accessible API for text generation. The API
can run on Render and call Groq; neither the PC nor Ollama needs to stay on.
The Groq key stays in the server's environment and must never be added to the
APK or committed to source control.

## Deploy the API

1. Create accounts with GitHub, Render, and Groq. Groq usage and model limits
   depend on the account and current provider terms.
2. Push this project to a private GitHub repository. Do not include API keys
   or `.env` files.
3. In Render, create a **Blueprint** from that repository and use `render.yaml`.
4. In the Render service's environment settings, provide:
   - `GROQ_API_KEY`: a key created in the Groq console.
   - `APP_ACCESS_CODE`: a private code used by the app. A 32-character
     hexadecimal value can be generated in PowerShell with
     `[guid]::NewGuid().ToString('N')`.
   - `GROQ_MODEL` is set to `openai/gpt-oss-120b`, a current Groq production
     model; check Groq's model catalog if it is unavailable on your account.
5. Wait for the service deployment to finish. Check its `/health` URL; it
   should return `{"status":"ok"}`. Copy the service's HTTPS URL.

Render's free service may sleep while idle and take longer to answer the first
request after waking. The API also limits generation requests to help protect
the Groq quota.

## Build and connect the Android app

From the project directory:

```powershell
npx cap copy android
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
Set-Location android
.\gradlew.bat assembleDebug
```

Install `android\app\build\outputs\apk\debug\app-debug.apk` on the phone.
In the app, enter the Render HTTPS URL and the `APP_ACCESS_CODE` configured
above, then tap **Verbinden**. The phone can use Wi-Fi or mobile data.

## Local development

Without `TEXT_PROVIDER=groq`, the backend continues to use Ollama at
`http://localhost:11434`. Start it with `python app.py` and open
`http://127.0.0.1:5000` in a desktop browser. The Android app requires an
HTTPS server URL; it cannot use a local HTTP backend.
