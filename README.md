# Historical Recipes Parser

Парсер исторических рецептов напитков из старинных источников.

## Функциональность

- Извлечение рецептов из Google Docs
- Парсинг исторических единиц измерения
- Конвертация в современные единицы
- Сохранение в PostgreSQL базу данных

## Установка

`ash
npm install
`

## Запуск

`ash
node test_runner.js
`

## Зависимости

- pg - для работы с PostgreSQL
- dotenv - для переменных окружения
- @supabase/supabase-js - для работы с Supabase
