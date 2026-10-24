import AuthLayout from '../layouts/AuthLayout';
import RegisterForm from '../features/auth/RegisterForm';

export function RegisterPage() {
  return (
    <AuthLayout title="Create account" subtitle="Register to open accounts and send payments.">
      <RegisterForm />
    </AuthLayout>
  );
}

export default RegisterPage;
