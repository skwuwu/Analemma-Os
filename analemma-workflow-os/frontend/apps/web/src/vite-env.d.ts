/// <reference types="vite/client" />

interface ImportMetaEnv {
	// AWS Cognito (injected via GitHub Secrets)
	readonly VITE_AWS_USER_POOL_ID?: string;
	readonly VITE_AWS_USER_POOL_WEB_CLIENT_ID?: string;
	readonly VITE_AWS_REGION?: string;

	// API Endpoints (injected via SSM Parameter Store in CI/CD)
	readonly VITE_API_BASE_URL?: string;
	readonly VITE_WS_URL?: string;

	// Lambda Function URL options (optional)
	readonly VITE_LFU_FUNCTION_URL?: string;
	readonly VITE_LFU_HTTPAPI_URL?: string;
	readonly VITE_LFU_USE_FUNCTION_URL?: string;

	// Legacy OIDC (optional, for alternative auth)
	readonly VITE_OIDC_ISSUER?: string;
	readonly VITE_OIDC_CLIENT_ID?: string;
	readonly VITE_OIDC_REDIRECT_URI?: string;
	readonly VITE_OIDC_SCOPE?: string;
}

interface ImportMeta {
	readonly env: ImportMetaEnv;
}
