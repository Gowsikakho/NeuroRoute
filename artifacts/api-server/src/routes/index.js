import { Router } from "express";
import healthRouter from "./health";
const router = Router();
router.use(healthRouter);
export default router;
