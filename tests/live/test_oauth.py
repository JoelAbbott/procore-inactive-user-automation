
from deactivation.oauth_manager import OAuthManager


if __name__=='__main__':
    oauth = OAuthManager()
    token = oauth.get_access_token()
    print(f"Access Token: {token}")
