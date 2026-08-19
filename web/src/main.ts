import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';

// The public demo has no identity-provider startup or browser token handling.
bootstrapApplication(App, appConfig).catch((err) => console.error(err));
