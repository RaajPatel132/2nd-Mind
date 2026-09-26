/**
 * The step catalogue (docs/design/system.md §8): one entry per step the backend can report.
 * Typed as `Record<AgentStep, StepSpec>`, so a new backend step without an entry fails the
 * type check.
 */
import {
  Bell,
  CalendarClock,
  CheckCheck,
  Database,
  GitCompare,
  Globe,
  ListOrdered,
  ListTree,
  MessageSquareText,
  Route,
  ScanText,
  Search,
  ShieldCheck,
  Tags,
  Undo2,
  Users,
} from 'lucide-react'
import type { AgentStep } from '../api/client'
import * as D from './details'
import * as L from './labels'
import * as R from './recall'
import * as RL from './recallLabels'
import type { StepSpec } from './types'

export const STEPS: Record<AgentStep, StepSpec> = {
  understand: {
    icon: ScanText,
    running: 'Reading your message',
    done: L.understandDone,
    chips: L.understandChips,
    Plain: D.UnderstandPlain,
    Tech: D.UnderstandTech,
  },
  extract: {
    icon: ListTree,
    running: 'Picking out what to remember',
    done: L.extractDone,
    chips: L.extractChips,
    Plain: D.ExtractPlain,
    Tech: D.ExtractTech,
  },
  dates: {
    icon: CalendarClock,
    running: 'Working out dates',
    done: L.datesDone,
    chips: L.datesChips,
    Plain: D.DatesPlain,
    Tech: D.DatesTech,
  },
  entities: {
    icon: Users,
    running: 'Recognising people, places and things',
    done: L.entitiesDone,
    chips: L.entitiesChips,
    Plain: D.EntitiesPlain,
    Tech: D.EntitiesTech,
  },
  reconcile: {
    icon: GitCompare,
    running: 'Checking what I already know',
    done: L.reconcileDone,
    chips: L.reconcileChips,
    Plain: D.ReconcilePlain,
    Tech: D.ReconcileTech,
  },
  enrich: {
    icon: Tags,
    running: 'Making it findable later',
    done: L.enrichDone,
    chips: L.enrichChips,
    Plain: D.EnrichPlain,
    Tech: D.EnrichTech,
  },
  guard: {
    icon: ShieldCheck,
    running: "Checking it's safe to keep",
    done: L.guardDone,
    chips: L.guardChips,
    Plain: D.GuardPlain,
    Tech: D.GuardTech,
  },
  save: {
    icon: Database,
    running: 'Saving',
    done: L.saveDone,
    chips: L.saveChips,
    Plain: D.SavePlain,
    Tech: D.DiffTech,
  },
  answer: {
    icon: MessageSquareText,
    running: 'Writing the reply',
    done: L.answerDone,
    chips: L.answerChips,
    Plain: D.AnswerPlain,
    Tech: D.AnswerTech,
  },
  undo: {
    icon: Undo2,
    running: 'Reverting that turn',
    done: L.undoDone,
    chips: L.diffChips,
    Plain: D.UndoPlain,
    Tech: D.DiffTech,
  },
  confirm: {
    icon: CheckCheck,
    running: 'Applying the change you approved',
    done: L.confirmDone,
    chips: L.diffChips,
    Plain: D.ConfirmPlain,
    Tech: D.DiffTech,
  },
  plan: {
    icon: Route,
    running: "Working out what you're asking",
    done: RL.planDone,
    chips: RL.planChips,
    Plain: R.PlanPlain,
    Tech: R.PlanTech,
  },
  search: {
    icon: Search,
    running: 'Searching your memory',
    done: RL.searchDone,
    chips: RL.searchChips,
    Plain: R.SearchPlain,
    Tech: R.SearchTech,
  },
  rank: {
    icon: ListOrdered,
    running: "Picking what's relevant",
    done: RL.rankDone,
    chips: RL.rankChips,
    Plain: R.RankPlain,
    Tech: R.RankTech,
  },
  triggers: {
    icon: Bell,
    running: 'Checking reminders tied to this',
    done: RL.triggersDone,
    chips: RL.triggersChips,
    Plain: R.TriggersPlain,
    Tech: R.TriggersTech,
  },
  fetch: {
    icon: Globe,
    running: 'Reading the link',
    done: () => 'Read the link',
    chips: L.none,
    Plain: D.ReservedPlain,
    Tech: D.ReservedTech,
  },
}
