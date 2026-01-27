import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Loader2, ArrowLeft } from 'lucide-react';
import { SignUpData } from '@/types/auth';
import { resetPassword, confirmResetPassword } from 'aws-amplify/auth';
import { toast } from 'sonner';

// --- Schemas (Password strength rules aligned with Cognito policy) ---
const passwordRules = z
  .string()
  .min(8, 'Password must be at least 8 characters.')
  .regex(/[0-9]/, 'Must include a number.')
  .regex(/[A-Z]/, 'Must include an uppercase letter.')
  .regex(/[a-z]/, 'Must include a lowercase letter.')
  .regex(/[^A-Za-z0-9]/, 'Must include a special character.');

const signInSchema = z.object({
  email: z.string().email('Please enter a valid email address.'),
  password: z.string().min(1, 'Please enter your password.'),
});

const signUpSchema = z.object({
  name: z.string().min(2, 'Name must be at least 2 characters.'),
  email: z.string().email('Please enter a valid email address.'),
  nickname: z.string().min(2, 'Nickname must be at least 2 characters.'),
  password: passwordRules,
});

const resetPasswordSchema = z.object({
  email: z.string().email('Please enter a valid email address.'),
});

const confirmResetSchema = z.object({
  email: z.string().email('Please enter a valid email address.'),
  code: z.string().min(6, 'Verification code must be 6 digits.'),
  newPassword: passwordRules,
});

// --- Types ---
type SignInFormValues = z.infer<typeof signInSchema>;
type SignUpFormValues = z.infer<typeof signUpSchema>;
type ResetPasswordFormValues = z.infer<typeof resetPasswordSchema>;
type ConfirmResetFormValues = z.infer<typeof confirmResetSchema>;

// --- Props (내부 로직 사용으로 불필요한 Props 제거) ---
interface AuthDialogProps {
  open: boolean;
  isLoading: boolean;
  onClose: () => void;
  onGoogleSignIn: () => void;
  onEmailSignIn: (email: string, password: string) => void;
  onSignUp: (data: SignUpData) => void;
}

export const AuthDialog = ({
  open,
  isLoading,
  onClose,
  onGoogleSignIn,
  onEmailSignIn,
  onSignUp,
}: AuthDialogProps) => {
  // 탭 상태와 뷰 모드를 분리하여 UX 개선
  const [activeTab, setActiveTab] = useState<string>('signin');
  const [isResetConfirmMode, setIsResetConfirmMode] = useState(false);

  // 1. Sign In Hook
  const {
    register: registerSignIn,
    handleSubmit: handleSubmitSignIn,
    formState: { errors: errorsSignIn },
  } = useForm<SignInFormValues>({ resolver: zodResolver(signInSchema) });

  // 2. Sign Up Hook
  const {
    register: registerSignUp,
    handleSubmit: handleSubmitSignUp,
    formState: { errors: errorsSignUp },
  } = useForm<SignUpFormValues>({ resolver: zodResolver(signUpSchema) });

  // 3. Reset Password Hook
  const {
    register: registerReset,
    handleSubmit: handleSubmitReset,
    formState: { errors: errorsReset },
    getValues: getResetValues, // 이메일 가져오기용
  } = useForm<ResetPasswordFormValues>({ resolver: zodResolver(resetPasswordSchema) });

  // 4. Confirm Reset Hook
  const {
    register: registerConfirm,
    handleSubmit: handleSubmitConfirm,
    formState: { errors: errorsConfirm },
    setValue: setConfirmValue, // 이메일 자동 세팅용
    reset: resetConfirmForm,
  } = useForm<ConfirmResetFormValues>({ resolver: zodResolver(confirmResetSchema) });

  // --- Handlers ---

  const onSignInSubmit = (data: SignInFormValues) => {
    onEmailSignIn(data.email, data.password);
  };

  const onSignUpSubmit = (data: SignUpFormValues) => {
    onSignUp(data as SignUpData);
  };

  const onResetSubmit = async (data: ResetPasswordFormValues) => {
    try {
      await resetPassword({ username: data.email });
      toast.success('Verification code has been sent to your email.');
      
      // [UX Improvement] Auto-fill email in next step form
      setConfirmValue('email', data.email);
      setIsResetConfirmMode(true); 
    } catch (error: any) {
      console.error('Reset password error:', error);
      toast.error(error.message || 'Failed to send verification code.');
    }
  };

  const onConfirmSubmit = async (data: ConfirmResetFormValues) => {
    try {
      await confirmResetPassword({
        username: data.email,
        confirmationCode: data.code,
        newPassword: data.newPassword,
      });
      toast.success('Password has been changed. Please sign in.');
      
      // Reset state and navigate to sign in tab
      setIsResetConfirmMode(false);
      resetConfirmForm();
      setActiveTab('signin');
    } catch (error: any) {
      console.error('Confirm reset password error:', error);
      toast.error(error.message || 'Failed to reset password.');
    }
  };

  // Clear reset mode when switching tabs
  const handleTabChange = (value: string) => {
    setActiveTab(value);
    if (value !== 'reset') setIsResetConfirmMode(false);
  };

  return (
    <Dialog open={open} onOpenChange={() => !isLoading && onClose()}>
      <DialogContent className="sm:max-w-md">
        <Tabs value={activeTab} onValueChange={handleTabChange}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="signin">Sign In</TabsTrigger>
            <TabsTrigger value="signup">Sign Up</TabsTrigger>
            <TabsTrigger value="reset">Reset PW</TabsTrigger>
          </TabsList>

          {/* --- Sign In Tab --- */}
          <TabsContent value="signin" className="space-y-4">
            <DialogHeader>
              <DialogTitle>Sign In</DialogTitle>
              <DialogDescription>Sign in to access the service.</DialogDescription>
            </DialogHeader>
            
            <Button
              variant="outline"
              className="w-full relative"
              onClick={onGoogleSignIn}
              disabled={isLoading}
            >
               {/* SVG Icon Simplified for brevity */}
              <svg className="mr-2 h-4 w-4" viewBox="0 0 24 24"><path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" /><path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" /><path fill="currentColor" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" /><path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" /></svg>
              Continue with Google
            </Button>

            <div className="relative">
              <div className="absolute inset-0 flex items-center"><Separator /></div>
              <div className="relative flex justify-center text-xs uppercase"><span className="bg-background px-2 text-muted-foreground">Or continue with</span></div>
            </div>

            <form onSubmit={handleSubmitSignIn(onSignInSubmit)} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="signin-email">Email</Label>
                <Input id="signin-email" type="email" disabled={isLoading} {...registerSignIn('email')} />
                {errorsSignIn.email && <p className="text-sm text-red-500">{errorsSignIn.email.message}</p>}
              </div>
              <div className="space-y-2">
                <Label htmlFor="signin-password">Password</Label>
                <Input id="signin-password" type="password" disabled={isLoading} {...registerSignIn('password')} />
                {errorsSignIn.password && <p className="text-sm text-red-500">{errorsSignIn.password.message}</p>}
              </div>
              <Button type="submit" className="w-full" disabled={isLoading}>
                {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Sign In
              </Button>
            </form>
          </TabsContent>

          {/* --- Sign Up Tab --- */}
          <TabsContent value="signup" className="space-y-4">
            <DialogHeader>
              <DialogTitle>Sign Up</DialogTitle>
              <DialogDescription>Create a new account.</DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmitSignUp(onSignUpSubmit)} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="signup-name">Name</Label>
                <Input id="signup-name" disabled={isLoading} {...registerSignUp('name')} />
                {errorsSignUp.name && <p className="text-xs text-red-500">{errorsSignUp.name.message}</p>}
              </div>
              <div className="space-y-2">
                <Label htmlFor="signup-nickname">Nickname</Label>
                <Input id="signup-nickname" disabled={isLoading} {...registerSignUp('nickname')} />
                {errorsSignUp.nickname && <p className="text-xs text-red-500">{errorsSignUp.nickname.message}</p>}
              </div>
              <div className="space-y-2">
                <Label htmlFor="signup-email">Email</Label>
                <Input id="signup-email" type="email" disabled={isLoading} {...registerSignUp('email')} />
                {errorsSignUp.email && <p className="text-xs text-red-500">{errorsSignUp.email.message}</p>}
              </div>
              <div className="space-y-2">
                <Label htmlFor="signup-password">Password</Label>
                <Input id="signup-password" type="password" disabled={isLoading} {...registerSignUp('password')} />
                {errorsSignUp.password && <p className="text-xs text-red-500">{errorsSignUp.password.message}</p>}
              </div>
              <Button type="submit" className="w-full" disabled={isLoading}>
                {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Sign Up
              </Button>
            </form>
          </TabsContent>

          {/* --- Reset Password Tab (Switchable View) --- */}
          <TabsContent value="reset" className="space-y-4">
            {!isResetConfirmMode ? (
              // Step 1: Enter email
              <>
                <DialogHeader>
                  <DialogTitle>Reset Password</DialogTitle>
                  <DialogDescription>We'll send a verification code to your registered email.</DialogDescription>
                </DialogHeader>
                <form onSubmit={handleSubmitReset(onResetSubmit)} className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="reset-email">Email</Label>
                    <Input id="reset-email" type="email" disabled={isLoading} {...registerReset('email')} />
                    {errorsReset.email && <p className="text-xs text-red-500">{errorsReset.email.message}</p>}
                  </div>
                  <Button type="submit" className="w-full" disabled={isLoading}>
                    {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    Send Verification Code
                  </Button>
                </form>
              </>
            ) : (
              // Step 2: Enter verification code and new password (Confirm)
              <>
                <DialogHeader>
                  <DialogTitle>Confirm Password Reset</DialogTitle>
                  <DialogDescription>Enter the code sent to your email and your new password.</DialogDescription>
                </DialogHeader>
                <form onSubmit={handleSubmitConfirm(onConfirmSubmit)} className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="confirm-email">Email</Label>
                    <Input 
                        id="confirm-email" 
                        type="email" 
                        disabled={true} // Email is read-only (UX)
                        className="bg-muted"
                        {...registerConfirm('email')} 
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="confirm-code">Verification Code (6 digits)</Label>
                    <Input id="confirm-code" placeholder="123456" disabled={isLoading} {...registerConfirm('code')} />
                    {errorsConfirm.code && <p className="text-xs text-red-500">{errorsConfirm.code.message}</p>}
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="confirm-password">New Password</Label>
                    <Input id="confirm-password" type="password" disabled={isLoading} {...registerConfirm('newPassword')} />
                    {errorsConfirm.newPassword && <p className="text-xs text-red-500">{errorsConfirm.newPassword.message}</p>}
                  </div>
                  <Button type="submit" className="w-full" disabled={isLoading}>
                    {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    Change Password
                  </Button>
                  
                  {/* Back button */}
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="w-full text-muted-foreground"
                    onClick={() => setIsResetConfirmMode(false)}
                    disabled={isLoading}
                  >
                    <ArrowLeft className="mr-2 h-4 w-4" />
                    Re-enter Email
                  </Button>
                </form>
              </>
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
};
