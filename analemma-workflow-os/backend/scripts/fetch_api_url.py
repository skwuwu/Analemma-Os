import boto3
import os
import sys

def fetch_and_update_env():
    region = os.environ.get('AWS_REGION', 'ap-northeast-2')
    ssm = boto3.client('ssm', region_name=region)

    api_param = "/my-app/dev/api-url"
    ws_param = "/my-app/dev/websocket-url"

    print(f"Fetching parameters from {region}...")

    try:
        # Fetch API URL
        try:
            api_url = ssm.get_parameter(Name=api_param, WithDecryption=True)['Parameter']['Value']
            print(f"Found API URL: {api_url}")
        except ssm.exceptions.ParameterNotFound:
            print(f"Warning: Parameter {api_param} not found.")
            api_url = None

        # Fetch WS URL
        try:
            ws_url = ssm.get_parameter(Name=ws_param, WithDecryption=True)['Parameter']['Value']
            print(f"Found WS URL: {ws_url}")
        except ssm.exceptions.ParameterNotFound:
            print(f"Warning: Parameter {ws_param} not found.")
            ws_url = None

        # Update .env file
        # Target: frontend/apps/web/.env (or .env.local)
        # Assuming script is run from backend/scripts, so ../../frontend/apps/web
        # But we will run it from project root or backend, so let's be careful.
        
        # We will assume this script is run from 'backend' directory, so target is ../frontend/apps/web/.env
        target_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../frontend/apps/web'))
        env_file = os.path.join(target_dir, '.env')
        
        if not os.path.exists(target_dir):
            print(f"Error: Target directory {target_dir} does not exist.")
            return

        print(f"Updating {env_file}...")

        env_lines = []
        if os.path.exists(env_file):
            with open(env_file, 'r', encoding='utf-8') as f:
                env_lines = f.readlines()

        new_lines = []
        found_api = False
        found_ws = False

        for line in env_lines:
            if line.startswith('VITE_API_BASE_URL='):
                if api_url:
                    new_lines.append(f"VITE_API_BASE_URL={api_url}\n")
                    found_api = True
                else:
                    new_lines.append(line) # Keep existing if fetch failed
            elif line.startswith('VITE_WS_URL='):
                if ws_url:
                    new_lines.append(f"VITE_WS_URL={ws_url}\n")
                    found_ws = True
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        if api_url and not found_api:
            if new_lines and not new_lines[-1].endswith('\n'):
                new_lines.append('\n')
            new_lines.append(f"VITE_API_BASE_URL={api_url}\n")

        if ws_url and not found_ws:
            if new_lines and not new_lines[-1].endswith('\n'):
                new_lines.append('\n')
            new_lines.append(f"VITE_WS_URL={ws_url}\n")

        with open(env_file, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

        print("Successfully updated .env file.")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    fetch_and_update_env()
