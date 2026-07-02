import "./globals.css";
import "@assistant-ui/react/styles/index.css";

export const metadata = { title: "Praxia" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
