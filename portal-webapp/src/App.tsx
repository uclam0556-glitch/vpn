import { useEffect, useState } from 'react';
import axios from 'axios';
import { Shield, Key, LogOut } from 'lucide-react';
import ResellerDashboard from './components/ResellerDashboard';
import AdminDashboard from './components/AdminDashboard';

export default function App() {
  const [accessKey, setAccessKey] = useState<string | null>(localStorage.getItem('portal_access_key'));
  const [inputKey, setInputKey] = useState('');
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const haptic = (style: 'light' | 'medium' | 'heavy' = 'light') => {
    // Web doesn't have Telegram haptic, we can use standard vibration API if supported
    if (navigator.vibrate) {
      if (style === 'light') navigator.vibrate(10);
      if (style === 'medium') navigator.vibrate(20);
      if (style === 'heavy') navigator.vibrate(40);
    }
  };

  useEffect(() => {
    if (accessKey) {
      checkAuth(accessKey);
    }
  }, [accessKey]);

  const checkAuth = async (key: string) => {
    setLoading(true);
    try {
      const res = await axios.get('/api/portal/me', {
        headers: { 'Authorization': `Bearer ${key}` }
      });
      setProfile(res.data);
    } catch (err) {
      console.error(err);
      handleLogout();
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputKey.trim()) return;
    setError('');
    localStorage.setItem('portal_access_key', inputKey.trim());
    setAccessKey(inputKey.trim());
  };

  const handleLogout = () => {
    localStorage.removeItem('portal_access_key');
    setAccessKey(null);
    setProfile(null);
  };

  if (!accessKey || !profile) {
    return (
      <div className="min-h-[100dvh] bg-background text-primary font-sans flex items-center justify-center p-4 relative overflow-hidden">
        {/* Aurora Live Background */}
        <div className="fixed inset-0 z-0 overflow-hidden pointer-events-none opacity-40">
          <div className="absolute top-[-10%] left-[-10%] w-[50vw] h-[50vw] rounded-full bg-accent/40 blur-[80px] animate-aurora mix-blend-screen" />
          <div className="absolute bottom-[-10%] right-[-10%] w-[60vw] h-[60vw] rounded-full bg-blue-500/30 blur-[100px] animate-aurora mix-blend-screen" style={{ animationDelay: '-5s', animationDuration: '20s' }} />
        </div>

        <div className="glass-card rounded-[32px] p-8 w-full max-w-md relative z-10 animate-in zoom-in-95 duration-300 shadow-2xl border border-white/10">
          <div className="w-16 h-16 rounded-full bg-accent/20 mx-auto flex items-center justify-center mb-6 border border-accent/30">
            <Shield className="w-8 h-8 text-accent" />
          </div>
          <h1 className="text-2xl font-bold text-center mb-2">HamaliVPN Portal</h1>
          <p className="text-muted text-center text-sm mb-8">Вход для партнеров</p>

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <div className="relative">
                <Key className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-muted" />
                <input
                  type="password"
                  placeholder="Ваш Access Key"
                  value={inputKey}
                  onChange={e => setInputKey(e.target.value)}
                  className="w-full bg-black/40 border border-white/10 rounded-2xl py-4 pl-12 pr-4 text-white placeholder-muted focus:outline-none focus:border-accent transition-colors backdrop-blur-sm"
                />
              </div>
              {error && <p className="text-red-400 text-xs mt-2">{error}</p>}
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-white text-black font-bold py-4 rounded-2xl hover:opacity-90 active:scale-95 transition-all shadow-[0_4px_20px_rgba(255,255,255,0.2)] disabled:opacity-50"
            >
              {loading ? 'Проверка...' : 'Войти'}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[100dvh] bg-background text-primary font-sans relative">
      {/* Aurora Live Background */}
      <div className="fixed inset-0 z-0 overflow-hidden pointer-events-none opacity-30">
        <div className="absolute top-[-10%] left-[-10%] w-[50vw] h-[50vw] rounded-full bg-accent/40 blur-[80px] animate-aurora mix-blend-screen" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[60vw] h-[60vw] rounded-full bg-blue-500/30 blur-[100px] animate-aurora mix-blend-screen" style={{ animationDelay: '-5s', animationDuration: '20s' }} />
      </div>

      <div className="relative z-10 max-w-4xl mx-auto p-4 pt-6">
        <div className="flex justify-end mb-4">
          <button onClick={handleLogout} className="flex items-center gap-2 text-sm text-muted hover:text-white transition-colors bg-white/5 px-4 py-2 rounded-xl">
            <LogOut className="w-4 h-4" /> Выйти
          </button>
        </div>

        {profile.role === 'super_admin' ? (
          <AdminDashboard profile={profile} haptic={haptic} />
        ) : profile.role === 'reseller' ? (
          <ResellerDashboard profile={profile} haptic={haptic} />
        ) : (
          <div className="glass-card rounded-[24px] p-8 text-center text-red-400">
            У вас нет доступа к порталу партнеров.
          </div>
        )}
      </div>
    </div>
  );
}
