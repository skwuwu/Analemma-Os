
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";

import { Amplify } from "aws-amplify";
import { CookieStorage } from 'aws-amplify/utils';
import { cognitoUserPoolsTokenProvider } from 'aws-amplify/auth/cognito';

type UnknownRecord = Record<string, unknown>;

function buildConfigFromEnv() {
	const userPoolId = import.meta.env.VITE_AWS_USER_POOL_ID;
	const userPoolClientId = import.meta.env.VITE_AWS_USER_POOL_WEB_CLIENT_ID;
	const region = import.meta.env.VITE_AWS_REGION || 'ap-northeast-2';

	// Cognito 설정 검증
	if (!userPoolId || !userPoolClientId) {
		console.error('❌ Missing Cognito configuration:', {
			userPoolId: !!userPoolId,
			userPoolClientId: !!userPoolClientId,
			region: !!region
		});
		throw new Error('Cognito configuration is incomplete. Check your .env file.');
	}

	// Amplify v6 호환 설정
	const cognito = {
		userPoolId,
		userPoolClientId,
		region,
		signUpVerificationMethod: 'code' as const,
		userAttributes: {
			email: {
				required: true,
			},
			name: {
				required: true,
			},
		},
		loginWith: {
			email: false,
			username: true,  // nickname을 username으로 처리
			phone: false,
		}
	};

	console.log('✅ Cognito configuration loaded:', {
		userPoolId,
		userPoolClientId,
		region
	});

	return {
		Auth: {
			Cognito: cognito
		}
	};
}

const config = buildConfigFromEnv();

// Amplify 설정 적용
try {
	Amplify.configure(config);
	console.log('✅ Amplify configured successfully');
} catch (error) {
	console.error('❌ Amplify configuration failed:', error);
	throw error;
}

// withAuthenticator와 호환되도록 CookieStorage 설정
try {
	console.log('🔧 Setting up CookieStorage for withAuthenticator compatibility');
	cognitoUserPoolsTokenProvider.setKeyValueStorage(new CookieStorage());
	console.log('✅ CookieStorage configured successfully');
} catch (error) {
	console.error('❌ CookieStorage setup failed:', error);
	// CookieStorage 설정 실패 시에도 계속 진행
}

// API Base URL 확인
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL;
console.log('🌐 API Configuration:', {
	VITE_API_BASE_URL: apiBaseUrl,
	isConfigured: !!apiBaseUrl
});

if (!apiBaseUrl) {
	console.warn('⚠️ VITE_API_BASE_URL is not set! API calls will fail.');
}

createRoot(document.getElementById("root")!).render(<App />);
