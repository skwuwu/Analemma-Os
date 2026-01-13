export interface AuthUser {
  username: string;
  userId: string;
  signInDetails?: {
    loginId?: string;
    authFlowType?: string;
  };
  [key: string]: unknown;
}

export interface SignUpData {
  name: string;
  email: string;
  nickname: string;
  password: string;
}
