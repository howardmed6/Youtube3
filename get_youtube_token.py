from google_auth_oauthlib.flow import InstalledAppFlow
import os

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

flow = InstalledAppFlow.from_client_secrets_file(
    'youtube_secret.json',
    scopes=['https://www.googleapis.com/auth/youtube.upload'],
    redirect_uri='urn:ietf:wg:oauth:2.0:oob'
)

auth_url, _ = flow.authorization_url(
    access_type='offline',
    prompt='consent'
)

print("Abre esta URL en tu navegador:")
print(auth_url)
print()
code = input("Pega aqui el codigo: ")

flow.fetch_token(code=code)
creds = flow.credentials
print("REFRESH TOKEN:", creds.refresh_token)