import { ArtifactStory } from "./components/ArtifactStory";
import { DemoSection } from "./components/DemoSection";
import { Footer } from "./components/Footer";
import { Hero } from "./components/Hero";
import { ImpactStats } from "./components/ImpactStats";
import { Navbar } from "./components/Navbar";

export default function App() {
  return (
    <div id="top" className="bg-grid">
      <Navbar />
      <main>
        <Hero />
        <DemoSection />
        <ArtifactStory />
        <ImpactStats />
      </main>
      <Footer />
    </div>
  );
}
