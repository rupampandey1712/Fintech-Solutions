import { alpha, createTheme, type Theme } from '@mui/material/styles';

export type ColorMode = 'light' | 'dark';

/** Brand tokens shared by the MUI theme and charts. CSS uses the matching custom properties. */
export const brand = {
  ink: '#0b1324',
  inkSoft: '#16213a',
  mint: '#34d3a6',
  teal: '#0ea5a4',
  violet: '#7c6cf6',
  sky: '#38bdf8',
  text: '#16213a',
  muted: '#64748b',
  line: '#e6eaf2',
};

export function createAppTheme(mode: ColorMode): Theme {
  const dark = mode === 'dark';
  return createTheme({
    palette: {
      mode,
      primary: { main: '#0f9f86', dark: '#0b7f6b', light: brand.mint, contrastText: '#ffffff' },
      secondary: { main: brand.violet, contrastText: '#ffffff' },
      error: { main: '#e5484d' },
      warning: { main: '#f5a524' },
      success: { main: '#12b886' },
      text: dark ? { primary: '#e6ebf5', secondary: '#94a3b8' } : { primary: brand.text, secondary: brand.muted },
      background: dark ? { default: '#070c18', paper: '#111a2e' } : { default: '#f4f6fb', paper: '#ffffff' },
      divider: dark ? '#22304a' : brand.line,
    },
    shape: { borderRadius: 12 },
    typography: {
      fontFamily: "'DM Sans', system-ui, sans-serif",
      h1: { fontFamily: 'Manrope, sans-serif' },
      h2: { fontFamily: 'Manrope, sans-serif' },
      h3: { fontFamily: 'Manrope, sans-serif' },
      h6: { fontFamily: 'Manrope, sans-serif', fontWeight: 700 },
      button: { textTransform: 'none', fontWeight: 700, letterSpacing: 0.1 },
    },
    components: {
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: {
            borderRadius: 11,
            paddingInline: 18,
            transition: 'transform .18s ease, box-shadow .25s ease, background-color .2s ease, border-color .2s ease',
            '&:active': { transform: 'translateY(0) scale(.98)' },
          },
          containedPrimary: {
            backgroundImage: `linear-gradient(135deg, ${brand.mint} 0%, #0f9f86 55%, ${brand.teal} 100%)`,
            boxShadow: `0 8px 20px -8px ${alpha('#0f9f86', 0.65)}`,
            '&:hover': {
              transform: 'translateY(-1px)',
              boxShadow: `0 14px 28px -10px ${alpha('#0f9f86', 0.7)}`,
            },
            // Flat and neutral, so a disabled button never reads as ready to click.
            '&.Mui-disabled': {
              backgroundImage: 'none',
              backgroundColor: dark ? '#1b2740' : '#e6eaf2',
              color: dark ? '#64748b' : '#8a96aa',
              boxShadow: 'none',
            },
          },
          outlined: {
            borderColor: dark ? '#2a3a57' : brand.line,
            backgroundColor: dark ? alpha('#111a2e', 0.7) : alpha('#ffffff', 0.7),
            backdropFilter: 'blur(6px)',
            '&:hover': {
              transform: 'translateY(-1px)',
              borderColor: dark ? '#3b4f73' : '#b9c4d6',
              backgroundColor: dark ? '#16213a' : '#ffffff',
            },
          },
        },
      },
      MuiOutlinedInput: {
        styleOverrides: {
          root: {
            borderRadius: 11,
            backgroundColor: dark ? alpha('#0b1324', 0.6) : alpha('#ffffff', 0.85),
            transition: 'box-shadow .2s ease',
            '&.Mui-focused': { boxShadow: `0 0 0 4px ${alpha(brand.mint, 0.18)}` },
          },
          notchedOutline: { borderColor: dark ? '#2a3a57' : brand.line },
        },
      },
      MuiDialog: {
        styleOverrides: {
          paper: { borderRadius: 20, boxShadow: '0 40px 80px -24px rgba(11,19,36,.45)', backgroundImage: 'none' },
        },
      },
      MuiBackdrop: {
        styleOverrides: {
          root: { '&:not(.MuiBackdrop-invisible)': { backgroundColor: alpha(brand.ink, 0.45), backdropFilter: 'blur(4px)' } },
        },
      },
      MuiDialogTitle: { styleOverrides: { root: { fontFamily: 'Manrope, sans-serif', fontWeight: 800 } } },
      MuiAlert: { styleOverrides: { root: { borderRadius: 12 } } },
      MuiMenu: {
        styleOverrides: {
          paper: {
            borderRadius: 14,
            border: `1px solid ${dark ? '#22304a' : brand.line}`,
            boxShadow: '0 20px 40px -16px rgba(11,19,36,.3)',
          },
        },
      },
      MuiTooltip: {
        styleOverrides: { tooltip: { backgroundColor: brand.ink, borderRadius: 8, fontSize: 11, fontWeight: 600 } },
      },
      MuiSkeleton: { defaultProps: { animation: 'wave' } },
      MuiTab: { styleOverrides: { root: { textTransform: 'none', fontWeight: 700 } } },
      MuiPaper: { styleOverrides: { root: { backgroundImage: 'none' } } },
    },
  });
}

/** Chart colours per mode. SVG presentation attributes cannot read CSS variables. */
export function chartColors(mode: ColorMode) {
  const dark = mode === 'dark';
  return {
    grid: dark ? '#22304a' : brand.line,
    axis: dark ? '#7d8ba3' : brand.muted,
    dotStroke: dark ? '#111a2e' : '#ffffff',
    track: dark ? '#22304a' : '#e5eaf1',
    tooltip: {
      borderRadius: 12,
      border: `1px solid ${dark ? '#22304a' : brand.line}`,
      background: dark ? '#111a2e' : '#ffffff',
      color: dark ? '#e6ebf5' : brand.text,
      boxShadow: '0 12px 30px -12px rgba(11,19,36,.35)',
    },
    primary: brand.mint,
    secondary: brand.violet,
    tertiary: brand.sky,
  };
}
