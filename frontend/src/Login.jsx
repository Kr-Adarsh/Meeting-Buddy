import React from 'react';
import { GoogleLogin } from '@react-oauth/google';
import { Bot, ShieldCheck } from 'lucide-react';
import { jwtDecode } from 'jwt-decode';

export default function Login({ setAuthToken, setUser }) {
  const handleSuccess = (credentialResponse) => {
    const token = credentialResponse.credential;
    const decoded = jwtDecode(token);
    localStorage.setItem('mb_token', token);
    localStorage.setItem('mb_user', JSON.stringify(decoded));
    setAuthToken(token);
    setUser(decoded);
  };

  return (
    <div className="app-container" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', background: 'var(--background)' }}>
      <div className="login-card" style={{ background: 'var(--surface)', padding: '3rem', borderRadius: 'var(--radius)', boxShadow: '0 20px 40px rgba(0,0,0,0.4)', textAlign: 'center', maxWidth: '400px', width: '90%' }}>
        <div style={{ background: 'rgba(139, 92, 246, 0.1)', width: '80px', height: '80px', borderRadius: '50%', display: 'flex', justifyContent: 'center', alignItems: 'center', margin: '0 auto 1.5rem auto' }}>
          <Bot size={40} color="var(--primary)" />
        </div>
        <h1 style={{ color: 'var(--text)', marginBottom: '0.5rem', fontSize: '24px' }}>Meeting Buddy</h1>
        <p style={{ color: 'var(--text-muted)', marginBottom: '2rem', fontSize: '15px' }}>
          Secure access completely locked down to authorized workspace operators.
        </p>

        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '1.5rem' }}>
          <GoogleLogin
            onSuccess={handleSuccess}
            onError={() => {
              console.log('Login Failed');
            }}
            useOneTap
            shape="pill"
            theme="filled_black"
          />
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px', color: 'var(--text-muted)', fontSize: '12px' }}>
          <ShieldCheck size={14} />
          <span>Protected by Google OAuth 2.0</span>
        </div>
      </div>
    </div>
  );
}
