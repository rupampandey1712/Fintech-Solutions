import AuthLayout from '../layouts/AuthLayout';
import LoginForm from '../features/auth/LoginForm';

export function LoginPage() {
  return (
    <AuthLayout title="Sign in" subtitle="Access the FinCore payments workspace.">
      <LoginForm />
    </AuthLayout>
  );
}

export default LoginPage;
