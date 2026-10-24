import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import { useAuth } from '../../hooks/useAuth';
import { problemMessage } from '../../utils/format';

const loginSchema = z.object({
  email: z.string().email('Enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
});

type LoginValues = z.infer<typeof loginSchema>;

export function LoginForm() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [serverError, setServerError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema), defaultValues: { email: '', password: '' } });

  async function onSubmit(values: LoginValues) {
    setServerError(null);
    try {
      await login(values.email, values.password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from ?? '/', { replace: true });
    } catch (error) {
      setServerError(problemMessage(error, 'Could not sign in. Check your credentials.'));
    }
  }

  return (
    <form className="auth-form" onSubmit={handleSubmit(onSubmit)} noValidate>
      {serverError ? <Alert severity="error">{serverError}</Alert> : null}
      <TextField
        label="Email"
        type="email"
        fullWidth
        autoComplete="email"
        error={Boolean(errors.email)}
        helperText={errors.email?.message}
        {...register('email')}
      />
      <TextField
        label="Password"
        type="password"
        fullWidth
        autoComplete="current-password"
        error={Boolean(errors.password)}
        helperText={errors.password?.message}
        {...register('password')}
      />
      <Button type="submit" variant="contained" size="large" disabled={isSubmitting} fullWidth>
        {isSubmitting ? 'Signing in…' : 'Sign in'}
      </Button>
    </form>
  );
}

export default LoginForm;
