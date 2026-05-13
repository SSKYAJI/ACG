import { ArtifactStory } from "./components/ArtifactStory";
import { DemoSection } from "./components/DemoSection";
import { Footer } from "./components/Footer";
import { Hero } from "./components/Hero";
import { Navbar } from "./components/Navbar";
import { TokenCalculator } from "./components/TokenCalculator";

export default function App() {
  return (
    <div id="top" className="bg-grid">
      <Navbar />
      <main>
        <Hero />
        <DemoSection />
        <ArtifactStory />
        <TokenCalculator />
      </main>
      <Footer />
    </div>
  );
}
