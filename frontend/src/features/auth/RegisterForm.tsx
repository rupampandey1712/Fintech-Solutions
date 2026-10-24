import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import LinearProgress from '@mui/material/LinearProgress';
import { useAuth } from '../../hooks/useAuth';
import { problemMessage } from '../../utils/format';

const registerSchema = z.object({
  email: z.string().email('Enter a valid email address'),
  password: z
    .string()
    .min(12, 'Password must be at least 12 characters')
    .regex(/[a-z]/, 'Include a lowercase letter')
    .regex(/[A-Z0-9]/, 'Include an uppercase letter or digit'),
});

type RegisterValues = z.infer<typeof registerSchema>;

function passwordStrength(pw: string): { score: number; label: string } {
  let score = 0;
  if (pw.length >= 12) score += 1;
  if (pw.length >= 16) score += 1;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score += 1;
  if (/\d/.test(pw)) score += 1;
  if (/[^A-Za-z0-9]/.test(pw)) score += 1;
  const labels = ['Too short', 'Weak', 'Fair', 'Good', 'Strong', 'Excellent'];
  return { score, label: labels[Math.min(score, labels.length - 1)] };
}

export function RegisterForm() {
  const { register: registerApi } = useAuth();
  const navigate = useNavigate();
  const [serverError, setServerError] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({ resolver: zodResolver(registerSchema), defaultValues: { email: '', password: '' } });

  const strength = passwordStrength(password);

  async function onSubmit(values: RegisterValues) {
    setServerError(null);
    try {
      await registerApi(values.email, values.password);
      navigate('/login', { replace: true, state: { registeredEmail: values.email } });
    } catch (error) {
      setServerError(problemMessage(error, 'Could not create the account.'));
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
        autoComplete="new-password"
        error={Boolean(errors.password)}
        helperText={errors.password?.message ?? 'Minimum 12 characters'}
        {...register('password', { onChange: (e) => setPassword(e.target.value) })}
      />
      {password ? (
        <Box>
          <LinearProgress
            variant="determinate"
            value={(strength.score / 5) * 100}
            color={strength.score <= 1 ? 'error' : strength.score <= 2 ? 'warning' : 'success'}
          />
          <p className="auth-strength">Strength: {strength.label}</p>
        </Box>
      ) : null}
      <Button type="submit" variant="contained" size="large" disabled={isSubmitting} fullWidth>
        {isSubmitting ? 'Creating account…' : 'Create account'}
      </Button>
    </form>
  );
}

export default RegisterForm;
