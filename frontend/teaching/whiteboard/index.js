import { renderMirrorTransform } from './mirror-transform.js';
import { renderCutFoldUnfold } from './cut-fold-unfold.js';
import { renderAttributeSort } from './attribute-sort.js';
import { renderMatchPairs } from './match-pairs.js';
import { renderSortOrder } from './sort-order.js';
import { renderShapeGuess } from './shape-guess.js';
import { renderPatternSequence } from './pattern-sequence.js';
import { renderGridExplorer } from './grid-explorer.js';
import { renderLogicGrid } from './logic-grid.js';
import { renderBarChart } from './bar-chart.js';
import { renderTimeScheduler } from './time-scheduler.js';
import { renderBalanceScale } from './balance-scale.js';
import { renderNumberGame } from './number-game.js';
import { renderStateMachine } from './state-machine.js';
import { renderPaperFoldExplorer } from './paper-fold-explorer.js';
import { renderWatermelonHalves } from './watermelon-halves.js';
import { renderDragToAnimal } from './drag-to-animal.js';
import { renderBrainwaveRacer } from './brainwave-racer.js';
import { renderBioBuilderLab } from './bio-builder-lab.js';
import { renderSkySignalRescue } from './sky-signal-rescue.js';

const interactiveSceneRegistry = {
  // Original primitives
  mirror_transform: renderMirrorTransform,
  cut_fold_unfold: renderCutFoldUnfold,
  attribute_sort: renderAttributeSort,
  // New primitives
  match_pairs: renderMatchPairs,
  sort_order: renderSortOrder,
  shape_guess: renderShapeGuess,
  pattern_sequence: renderPatternSequence,
  grid_explorer: renderGridExplorer,
  logic_grid: renderLogicGrid,
  bar_chart: renderBarChart,
  time_scheduler: renderTimeScheduler,
  balance_scale: renderBalanceScale,
  number_game: renderNumberGame,
  state_machine: renderStateMachine,
  paper_fold_explorer: renderPaperFoldExplorer,
  watermelon_halves: renderWatermelonHalves,
  drag_to_animal: renderDragToAnimal,
  brainwave_racer: renderBrainwaveRacer,
  bio_builder_lab: renderBioBuilderLab,
  sky_signal_rescue: renderSkySignalRescue,
};

export function renderInteractiveScene(scene, interactiveSceneEl = document.getElementById('interactive-scene')){
  if (!interactiveSceneEl) return;
  const renderer = interactiveSceneRegistry[scene && scene.primitive];
  if (!renderer) {
    interactiveSceneEl.hidden = true;
    return;
  }
  interactiveSceneEl.hidden = false;
  const head = document.createElement('div');
  head.className = 'scene-head';
  const titleBox = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'scene-title';
  title.textContent = scene.title || '互动探索';
  const prompt = document.createElement('div');
  prompt.className = 'scene-prompt';
  prompt.textContent = scene.prompt || '动手试试看。';
  titleBox.append(title, prompt);
  const goal = document.createElement('div');
  goal.className = 'scene-goal';
  goal.textContent = scene.goal || '';
  head.append(titleBox, goal);
  interactiveSceneEl.appendChild(head);
  const parts = renderer(scene);
  parts.forEach(part => interactiveSceneEl.appendChild(part));
}

window.renderInteractiveScene = renderInteractiveScene;
