import { useAuth } from "./auth";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { Space } from "./pages/Space";
import { Link, Redirect, useRouter } from "./router";

export function App() {
  const { user, loading } = useAuth();
  const { path } = useRouter();

  if (loading) {
    return (
      <div className="center" role="status">
        Chargement…
      </div>
    );
  }

  switch (path) {
    case "/":
      return <Redirect to={user ? "/espace" : "/connexion"} />;
    case "/connexion":
      return user ? <Redirect to="/espace" /> : <Login />;
    case "/inscription":
      return user ? <Redirect to="/espace" /> : <Register />;
    case "/espace":
      return user ? <Space /> : <Redirect to="/connexion" />;
    default:
      return (
        <div className="center">
          <h1>Page introuvable</h1>
          <Link to="/">Retour à l'accueil</Link>
        </div>
      );
  }
}
