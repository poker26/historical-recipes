#!/usr/bin/env node

// Финальный скрипт для извлечения и загрузки исторических рецептов напитков
// Запуск: node runner.js

import { HistoricalRecipeParser, processHistoricalDocument } from './historical_recipes_parser.js';
import { config } from 'dotenv';
import fs from 'fs/promises';

config();

// Конфигурация
const CONFIG = {
  googleDocsId: '1XO0_BqH-e4B-_F4hrzKKuaX6w8CR2IARgvAsfjQnAKA',
  supabase: {
    url: process.env.SUPABASE_URL,
    key: process.env.SUPABASE_ANON_KEY
  }
};

// Функция для извлечения текста из Google Docs
async function fetchGoogleDocsText(documentId) {
  const exportUrl = `https://docs.google.com/document/d/${documentId}/export?format=txt`;
  
  try {
    console.log('📄 Извлекаем текст из Google Docs...');
    const response = await fetch(exportUrl);
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const text = await response.text();
    console.log(`📊 Извлечено ${text.length} символов`);
    
    return text;
  } catch (error) {
    console.error('❌ Ошибка доступа к Google Docs:', error.message);
    throw error;
  }
}

// Функция для чтения локального файла (альтернатива)
async function readLocalFile(filepath) {
  try {
    console.log(`📁 Читаем локальный файл: ${filepath}`);
    const text = await fs.readFile(filepath, 'utf8');
    console.log(`📊 Прочитано ${text.length} символов`);
    return text;
  } catch (error) {
    console.error('❌ Ошибка чтения файла:', error.message);
    throw error;
  }
}

// Валидация конфигурации
function validateConfig() {
  const missing = [];
  
  if (!CONFIG.supabase.url) missing.push('SUPABASE_URL');
  if (!CONFIG.supabase.key) missing.push('SUPABASE_ANON_KEY');
  
  if (missing.length > 0) {
    console.error('❌ Не хватает переменных окружения:', missing.join(', '));
    console.log('💡 Создайте файл .env с:');
    console.log('   SUPABASE_URL=https://your-project.supabase.co');
    console.log('   SUPABASE_ANON_KEY=your-anon-key');
    process.exit(1);
  }
}

// Показать SQL для создания таблицы
function showCreateTableSQL() {
  console.log('\n📋 SQL ДЛЯ СОЗДАНИЯ ТАБЛИЦЫ В SUPABASE:');
  console.log('='.repeat(60));
  console.log(`
-- Выполните этот SQL в Supabase Dashboard -> SQL Editor

CREATE TABLE IF NOT EXISTS drinks_recipes (
  id SERIAL PRIMARY KEY,
  recipe_number INTEGER,
  name TEXT NOT NULL,
  ingredients JSONB NOT NULL DEFAULT '[]'::jsonb,
  ingredients_per_liter JSONB NOT NULL DEFAULT '[]'::jsonb,
  description TEXT,
  original_text TEXT,
  source_type TEXT DEFAULT 'historical',
  historical_measures JSONB DEFAULT '[]'::jsonb,
  alcohol_content DECIMAL(5,2),
  preparation_method TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Создание индексов для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_drinks_name 
ON drinks_recipes USING gin (to_tsvector('russian', name));

CREATE INDEX IF NOT EXISTS idx_drinks_description 
ON drinks_recipes USING gin (to_tsvector('russian', description));

CREATE INDEX IF NOT EXISTS idx_drinks_ingredients 
ON drinks_recipes USING gin (ingredients);

CREATE INDEX IF NOT EXISTS idx_drinks_recipe_number 
ON drinks_recipes (recipe_number);

CREATE INDEX IF NOT EXISTS idx_drinks_source_type 
ON drinks_recipes (source_type);

-- Функция для автоматического обновления updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$ language 'plpgsql';

-- Триггер для обновления времени изменения
DROP TRIGGER IF EXISTS update_drinks_recipes_updated_at ON drinks_recipes;
CREATE TRIGGER update_drinks_recipes_updated_at 
  BEFORE UPDATE ON drinks_recipes 
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Функция для поиска по ингредиентам
CREATE OR REPLACE FUNCTION search_recipes_by_ingredient(ingredient_name TEXT)
RETURNS TABLE(
  id INTEGER,
  name TEXT,
  recipe_number INTEGER,
  matching_ingredients TEXT[]
) AS $
BEGIN
  RETURN QUERY
  SELECT 
    dr.id,
    dr.name,
    dr.recipe_number,
    ARRAY(
      SELECT jsonb_extract_path_text(ing, 'name')
      FROM jsonb_array_elements(dr.ingredients) ing
      WHERE jsonb_extract_path_text(ing, 'name') ILIKE '%' || ingredient_name || '%'
    ) as matching_ingredients
  FROM drinks_recipes dr
  WHERE dr.ingredients::text ILIKE '%' || ingredient_name || '%';
END;
$ LANGUAGE plpgsql;

-- Функция для получения статистики ингредиентов
CREATE OR REPLACE FUNCTION get_ingredient_statistics()
RETURNS TABLE(
  ingredient_name TEXT,
  usage_count BIGINT,
  avg_amount NUMERIC,
  unit TEXT
) AS $
BEGIN
  RETURN QUERY
  SELECT 
    jsonb_extract_path_text(ing, 'name') as ingredient_name,
    COUNT(*) as usage_count,
    AVG((jsonb_extract_path_text(ing, 'modern_amount'))::NUMERIC) as avg_amount,
    jsonb_extract_path_text(ing, 'modern_unit') as unit
  FROM drinks_recipes dr,
       jsonb_array_elements(dr.ingredients) ing
  WHERE jsonb_extract_path_text(ing, 'name') IS NOT NULL
  GROUP BY jsonb_extract_path_text(ing, 'name'), jsonb_extract_path_text(ing, 'modern_unit')
  HAVING COUNT(*) > 1
  ORDER BY usage_count DESC, ingredient_name;
END;
$ LANGUAGE plpgsql;
  `);
  console.log('='.repeat(60));
}

// Функция тестирования парсера (без загрузки в БД)
async function testParser(documentText) {
  console.log('🧪 РЕЖИМ ТЕСТИРОВАНИЯ');
  console.log('='.repeat(50));
  
  const parser = new HistoricalRecipeParser();
  const recipes = parser.parseHistoricalRecipes(documentText);
  
  console.log(`📊 Результат: найдено ${recipes.length} рецептов\n`);
  
  recipes.slice(0, 3).forEach((recipe, index) => {
    console.log(`📜 Рецепт ${index + 1}:`);
    console.log(`   Номер: ${recipe.recipe_number}`);
    console.log(`   Название: ${recipe.name}`);
    console.log(`   Ингредиентов: ${recipe.ingredients.length}`);
    
    console.log('   Ингредиенты:');
    recipe.ingredients.forEach(ing => {
      console.log(`     • ${ing.name}: ${ing.amount} ${ing.unit} (${ing.modern_amount} ${ing.modern_unit})`);
    });
    
    console.log(`   Описание: ${recipe.description?.substring(0, 100) || 'Не извлечено'}...`);
    console.log('');
  });
  
  if (recipes.length > 3) {
    console.log(`   ... и ещё ${recipes.length - 3} рецептов`);
  }
  
  return recipes;
}

// Главная функция
async function main() {
  console.log('🍺 ИЗВЛЕЧЕНИЕ ИСТОРИЧЕСКИХ РЕЦЕПТОВ НАПИТКОВ');
  console.log('='.repeat(60));
  
  try {
    // Проверяем конфигурацию
    validateConfig();
    
    // Получаем аргументы командной строки
    const args = process.argv.slice(2);
    const testMode = args.includes('--test') || args.includes('-t');
    const showSQL = args.includes('--sql') || args.includes('-s');
    const useLocalFile = args.find(arg => arg.startsWith('--file='));
    const helpMode = args.includes('--help') || args.includes('-h');
    
    // Показываем помощь
    if (helpMode) {
      console.log(`
📖 ИСПОЛЬЗОВАНИЕ:
  node runner.js [опции]

🔧 ОПЦИИ:
  --test, -t           Только тестирование парсера (без загрузки в БД)
  --sql, -s            Показать SQL для создания таблицы
  --file=path          Использовать локальный файл вместо Google Docs
  --help, -h           Показать эту справку

📝 ПРИМЕРЫ:
  node runner.js                    # Полная обработка из Google Docs
  node runner.js --test             # Только тестирование
  node runner.js --sql              # Показать SQL для БД
  node runner.js --file=recipes.txt # Обработка локального файла
      `);
      return;
    }
    
    // Показываем SQL если требуется
    if (showSQL) {
      showCreateTableSQL();
      return;
    }
    
    // Получаем текст документа
    let documentText;
    if (useLocalFile) {
      const filepath = useLocalFile.split('=')[1];
      documentText = await readLocalFile(filepath);
    } else {
      documentText = await fetchGoogleDocsText(CONFIG.googleDocsId);
    }
    
    if (!documentText || documentText.length < 100) {
      throw new Error('Документ слишком короткий или пустой');
    }
    
    // Режим тестирования
    if (testMode) {
      await testParser(documentText);
      console.log('\n✅ Тестирование завершено');
      console.log('💡 Для загрузки в БД запустите без параметра --test');
      return;
    }
    
    // Полная обработка
    console.log('🚀 Запускаем полную обработку...');
    showCreateTableSQL();
    
    console.log('\n⚠️  ПЕРЕД ПРОДОЛЖЕНИЕМ:');
    console.log('1. Выполните SQL выше в Supabase Dashboard');
    console.log('2. Нажмите Enter для продолжения или Ctrl+C для отмены');
    
    // Ждем подтверждения пользователя
    process.stdin.setRawMode(true);
    process.stdin.resume();
    await new Promise(resolve => {
      process.stdin.once('data', () => {
        process.stdin.setRawMode(false);
        resolve();
      });
    });
    
    // Обрабатываем документ
    const result = await processHistoricalDocument(documentText);
    
    console.log('\n🎉 ПРОЦЕСС ЗАВЕРШЕН УСПЕШНО!');
    console.log(`📊 Статистика:`);
    console.log(`   • Всего рецептов: ${result.totalRecipes || 0}`);
    console.log(`   • Загружено: ${result.uploaded || 0}`);
    console.log(`   • Ошибок: ${result.errors || 0}`);
    
    if (result.uploaded > 0) {
      console.log('\n🔍 Примеры SQL-запросов для работы с данными:');
      console.log(`
-- Поиск рецептов с водкой:
SELECT name, recipe_number FROM drinks_recipes 
WHERE ingredients::text ILIKE '%водка%';

-- Поиск по ингредиенту:
SELECT * FROM search_recipes_by_ingredient('анис');

-- Статистика ингредиентов:
SELECT * FROM get_ingredient_statistics() LIMIT 10;

-- Рецепты с наибольшим количеством ингредиентов:
SELECT name, jsonb_array_length(ingredients) as ingredient_count 
FROM drinks_recipes 
ORDER BY ingredient_count DESC LIMIT 5;
      `);
    }
    
  } catch (error) {
    console.error('\n💥 КРИТИЧЕСКАЯ ОШИБКА:', error.message);
    
    if (error.message.includes('403') || error.message.includes('Forbidden')) {
      console.log('\n🔧 РЕШЕНИЕ:');
      console.log('1. Откройте Google Docs');
      console.log('2. Нажмите "Настройки доступа" (Поделиться)');
      console.log('3. Выберите "Просмотр для всех, у кого есть ссылка"');
    }
    
    if (error.message.includes('SUPABASE') || error.message.includes('API')) {
      console.log('\n🔧 РЕШЕНИЕ:');
      console.log('1. Проверьте файл .env');
      console.log('2. Убедитесь, что SUPABASE_URL и SUPABASE_ANON_KEY корректные');
      console.log('3. Проверьте доступ к интернету');
    }
    
    process.exit(1);
  }
}

// Обработка сигналов завершения
process.on('SIGINT', () => {
  console.log('\n\n👋 Процесс прерван пользователем');
  process.exit(0);
});

process.on('SIGTERM', () => {
  console.log('\n\n👋 Процесс завершен');
  process.exit(0);
});

// Запуск если файл выполняется напрямую
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(error => {
    console.error('Необработанная ошибка:', error);
    process.exit(1);
  });
}

export { main, testParser, fetchGoogleDocsText, readLocalFile };