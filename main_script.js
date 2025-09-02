#!/usr/bin/env node

// Адаптированный скрипт для прямого подключения к PostgreSQL (Supabase)
// Установка зависимостей: npm install pg dotenv

import pg from 'pg';
import { config } from 'dotenv';
import fs from 'fs/promises';

const { Pool } = pg;
config();

// Конфигурация базы данных
const dbConfig = {
  user: 'postgres.xkwgspqwebfeteoblayu',        
  host: 'aws-0-eu-north-1.pooler.supabase.com',
  database: 'postgres',   
  password: 'Gopapopa326+',    
  port: 6543,
  ssl: {
    rejectUnauthorized: false
  }
};

class HistoricalRecipeParser {
  constructor(pool) {
    this.pool = pool;
    this.HISTORICAL_CONVERSIONS = {
      'штоф': 1.23, 'золотник': 4.266, 'фунт': 409.5,
      'ведро': 12.3, 'бутыль': 0.75, 'стакан': 0.25, 'чарка': 0.123,
      'лот': 12.8, 'пуд': 16380, 'литр': 1, 'л': 1, 'грамм': 1, 'г': 1
    };
  }

  parseHistoricalNumber(numStr) {
    if (!numStr) return 0;
    numStr = numStr.trim();
    
    const fractionMatch = numStr.match(/(\d+)\s+(\d+)\/(\d+)/);
    if (fractionMatch) {
      const whole = parseInt(fractionMatch[1]);
      const numerator = parseInt(fractionMatch[2]);
      const denominator = parseInt(fractionMatch[3]);
      return whole + (numerator / denominator);
    }
    
    const simpleFractionMatch = numStr.match(/(\d+)\/(\d+)/);
    if (simpleFractionMatch) {
      const numerator = parseInt(simpleFractionMatch[1]);
      const denominator = parseInt(simpleFractionMatch[2]);
      return numerator / denominator;
    }
    
    return parseFloat(numStr) || 0;
  }

  cleanIngredientName(name) {
    if (!name) return '';
    
    return name
      .toLowerCase()
      .replace(/[,;.()!]+$/, '')
      .replace(/^\s*-\s*/, '')
      .replace(/\s+/g, ' ')
      .replace(/^(свежей?|сухой?|молотой?|целой?|мелкой?|лучшей?)\s+/, '')
      .replace(/\s+(свежий|сухой|молотый|целый|мелкий|лучший)$/, '')
      .replace(/\s*\(.*?\)\s*/g, '')
      .replace(/около\s*[\d,]+\s*[гл]/, '')
      .trim();
  }

  parseHistoricalIngredients(text) {
    const ingredients = [];
    
    const patterns = [
      // Паттерн 1: название — число золотника (около число г)
      {
        regex: /([а-яё][а-яёa-z\s\-,()]+?)\s*—\s*(\d+(?:\s*\d*\/\d+)?)\s*золотник[а-я]*\s*\([^)]*?([\d,]+)\s*г\)/gi,
        parse: (match) => ({
          name: this.cleanIngredientName(match[1]),
          amount: this.parseHistoricalNumber(match[2]),
          unit: 'золотник',
          modern_amount: parseFloat(match[3].replace(',', '.')),
          modern_unit: 'г'
        })
      },
      
      // Паттерн 2: число штофа/штофами (около число л) названия
      {
        regex: /(\d+(?:\s*\d*\/\d+)?)\s*штоф[а-я]*\s*\([^)]*?([\d,]+)\s*л\)\s*([а-яё\s\-,()]+?)(?=[.;,!\n]|$)/gi,
        parse: (match) => ({
          name: this.cleanIngredientName(match[3]),
          amount: this.parseHistoricalNumber(match[1]),
          unit: 'штоф',
          modern_amount: parseFloat(match[2].replace(',', '.')),
          modern_unit: 'л'
        })
      },
      
      // Паттерн 3: число фунта (около число г) названия
      {
        regex: /(\d+(?:\s*\d*\/\d+)?)\s*фунт[а-я]*\s*\([^)]*?([\d,]+)\s*г\)\s*([а-яё\s\-,()]+?)(?=[.;,!\n]|$)/gi,
        parse: (match) => ({
          name: this.cleanIngredientName(match[3]),
          amount: this.parseHistoricalNumber(match[1]),
          unit: 'фунт',
          modern_amount: parseFloat(match[2].replace(',', '.')),
          modern_unit: 'г'
        })
      },
      
      // Паттерн 4: список "по X золотника"
      {
        regex: /([а-яё\s\-,()]+?),\s*([а-яё\s\-,()]+?)(?:,\s*([а-яё\s\-,()]+?))?(?:,\s*([а-яё\s\-,()]+?))?\s*—\s*по\s*(\d+(?:\s*\d*\/\d+)?)\s*золотник[а-я]*\s*\([^)]*?([\d,]+)\s*г\)/gi,
        parse: (match) => {
          const amount = this.parseHistoricalNumber(match[5]);
          const modernAmount = parseFloat(match[6].replace(',', '.'));
          const results = [];
          
          for (let i = 1; i <= 4; i++) {
            if (match[i] && match[i].trim() && match[i].trim().length > 2) {
              const cleanName = this.cleanIngredientName(match[i]);
              if (cleanName && cleanName !== 'г' && !cleanName.includes('около')) {
                results.push({
                  name: cleanName,
                  amount: amount,
                  unit: 'золотник',
                  modern_amount: modernAmount,
                  modern_unit: 'г'
                });
              }
            }
          }
          return results;
        }
      }
    ];
    
    patterns.forEach((pattern, patternIndex) => {
      if (!pattern.parse) return;
      
      let match;
      const regex = new RegExp(pattern.regex.source, pattern.regex.flags);
      
      while ((match = regex.exec(text)) !== null) {
        try {
          const result = pattern.parse(match);
          if (Array.isArray(result)) {
            result.forEach(item => {
              if (item && item.name && item.name.length > 1) {
                ingredients.push(item);
              }
            });
          } else if (result && result.name && result.name.length > 1) {
            ingredients.push(result);
          }
        } catch (error) {
          console.warn(`Ошибка парсинга паттерна ${patternIndex + 1}:`, error.message);
        }
      }
    });

    this.findMainIngredients(text, ingredients);
    return this.cleanIngredients(ingredients);
  }

  findMainIngredients(text, ingredients) {
    const existingNames = new Set(ingredients.map(ing => ing.name.toLowerCase()));
    
    // Поиск водки в штофах
    const vodkaPattern = /(\d+(?:\s*\d*\/\d+)?)\s*штоф[а-я]*.*?водк[а-я]*/gi;
    let match;
    while ((match = vodkaPattern.exec(text)) !== null) {
      if (!existingNames.has('водка')) {
        const shtofs = this.parseHistoricalNumber(match[1]);
        ingredients.push({
          name: 'водка',
          amount: shtofs,
          unit: 'штоф',
          modern_amount: shtofs * this.HISTORICAL_CONVERSIONS['штоф'],
          modern_unit: 'л'
        });
        existingNames.add('водка');
      }
    }

    // Поиск сахара
    const sugarPattern = /(\d+(?:\s*\d*\/\d+)?)\s*фунт[а-я]*.*?сахар[а-я]*/gi;
    while ((match = sugarPattern.exec(text)) !== null) {
      if (!existingNames.has('сахар')) {
        const pounds = this.parseHistoricalNumber(match[1]);
        ingredients.push({
          name: 'сахар',
          amount: pounds,
          unit: 'фунт',
          modern_amount: pounds * this.HISTORICAL_CONVERSIONS['фунт'],
          modern_unit: 'г'
        });
        existingNames.add('сахар');
      }
    }

    // Поиск воды
    const waterPattern = /(\d+(?:\s*\d*\/\d+)?)\s*штоф[а-я]*.*?вод[ыеу]/gi;
    while ((match = waterPattern.exec(text)) !== null) {
      if (!existingNames.has('вода')) {
        const shtofs = this.parseHistoricalNumber(match[1]);
        ingredients.push({
          name: 'вода',
          amount: shtofs,
          unit: 'штоф',
          modern_amount: shtofs * this.HISTORICAL_CONVERSIONS['штоф'],
          modern_unit: 'л'
        });
        existingNames.add('вода');
      }
    }
  }

  cleanIngredients(ingredients) {
    const cleanedIngredients = ingredients
      .filter(ing => ing && ing.name && ing.name.length > 1)
      .map(ing => ({
        ...ing,
        name: this.cleanIngredientName(ing.name)
      }));

    const uniqueIngredients = new Map();
    cleanedIngredients.forEach(ing => {
      const key = ing.name.toLowerCase();
      if (uniqueIngredients.has(key)) {
        const existing = uniqueIngredients.get(key);
        if (existing.modern_unit === ing.modern_unit) {
          existing.modern_amount += ing.modern_amount;
        }
      } else {
        uniqueIngredients.set(key, ing);
      }
    });

    return Array.from(uniqueIngredients.values());
  }

  extractHistoricalDescription(text) {
    const descriptionPattern = /(?:Истолочь|Покрошив|Раздробив|После)([\s\S]*?)$/i;
    const match = descriptionPattern.exec(text);
    
    if (match) {
      return match[1].trim().replace(/\s+/g, ' ');
    }
    
    const takePattern = /Возьмите:[\s\S]*?(?=\n\n|$)([\s\S]*?)$/i;
    const takeMatch = takePattern.exec(text);
    
    if (takeMatch) {
      return takeMatch[1].trim().replace(/\s+/g, ' ');
    }
    
    return text.trim();
  }

  calculatePerLiterHistorical(ingredients, recipeText) {
    let totalAlcoholVolume = 0;
    
    ingredients.forEach(ing => {
      if (ing.modern_unit === 'л' && 
          (ing.name.includes('водк') || ing.name.includes('спирт'))) {
        totalAlcoholVolume += ing.modern_amount;
      }
    });
    
    if (totalAlcoholVolume === 0) {
      const shtofPattern = /(\d+(?:\s*\d*\/\d+)?)\s*штоф[а-я]*.*?водк/gi;
      let match;
      while ((match = shtofPattern.exec(recipeText)) !== null) {
        const shtofs = this.parseHistoricalNumber(match[1]);
        totalAlcoholVolume += shtofs * this.HISTORICAL_CONVERSIONS['штоф'];
      }
    }
    
    if (totalAlcoholVolume === 0) {
      totalAlcoholVolume = 1;
    }
    
    return ingredients.map(ing => ({
      ...ing,
      amount_per_liter: (ing.modern_amount / totalAlcoholVolume),
      unit_per_liter: ing.modern_unit,
      base_volume: totalAlcoholVolume
    }));
  }

  parseHistoricalRecipes(text) {
    const recipes = [];
    const recipePattern = /\*\*(\d+)\.\s*([^*]+?)\.\*\*([\s\S]*?)(?=\*\*\d+\.|$)/g;
    
    let match;
    while ((match = recipePattern.exec(text)) !== null) {
      const number = parseInt(match[1]);
      const name = match[2].trim();
      const content = match[3].trim();
      
      if (content.length < 100) {
        console.log(`Пропущен короткий текст для "${name}"`);
        continue;
      }
      
      console.log(`Обрабатываем рецепт ${number}: "${name}"`);
      
      const ingredients = this.parseHistoricalIngredients(content);
      const description = this.extractHistoricalDescription(content);
      
      if (ingredients.length > 0) {
        const ingredientsPerLiter = this.calculatePerLiterHistorical(ingredients, content);
        
        const recipe = {
          recipe_number: number,
          name: name,
          ingredients: JSON.stringify(ingredients),
          ingredients_per_liter: JSON.stringify(ingredientsPerLiter),
          description: description,
          original_text: content,
          source_type: 'historical',
          historical_measures: JSON.stringify(ingredients.map(ing => ({
            name: ing.name,
            historical_amount: ing.amount,
            historical_unit: ing.unit,
            modern_equivalent: `${ing.modern_amount} ${ing.modern_unit}`
          }))),
          created_at: new Date().toISOString()
        };
        
        recipes.push(recipe);
        console.log(`✅ Найдено ${ingredients.length} ингредиентов в рецепте "${name}"`);
      } else {
        console.warn(`⚠️ Не найдены ингредиенты в рецепте "${name}"`);
      }
    }
    
    return recipes;
  }

  async createTable() {
    const createTableSQL = `
      DROP TABLE IF EXISTS drinks_recipes CASCADE;
      
      CREATE TABLE drinks_recipes (
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

      CREATE INDEX idx_drinks_name ON drinks_recipes USING gin (to_tsvector('russian', name));
      CREATE INDEX idx_drinks_description ON drinks_recipes USING gin (to_tsvector('russian', description));
      CREATE INDEX idx_drinks_ingredients ON drinks_recipes USING gin (ingredients);
      CREATE INDEX idx_drinks_recipe_number ON drinks_recipes (recipe_number);
      CREATE INDEX idx_drinks_source_type ON drinks_recipes (source_type);
      
      CREATE OR REPLACE FUNCTION update_updated_at_column()
      RETURNS TRIGGER AS $$
      BEGIN
        NEW.updated_at = NOW();
        RETURN NEW;
      END;
      $$ language 'plpgsql';

      DROP TRIGGER IF EXISTS update_drinks_recipes_updated_at ON drinks_recipes;
      CREATE TRIGGER update_drinks_recipes_updated_at 
        BEFORE UPDATE ON drinks_recipes 
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

      CREATE OR REPLACE FUNCTION search_recipes_by_ingredient(ingredient_name TEXT)
      RETURNS TABLE(
        id INTEGER,
        name TEXT,
        recipe_number INTEGER,
        matching_ingredients TEXT[]
      ) AS $$
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
      $$ LANGUAGE plpgsql;

      CREATE OR REPLACE FUNCTION get_ingredient_statistics()
      RETURNS TABLE(
        ingredient_name TEXT,
        usage_count BIGINT,
        avg_amount NUMERIC,
        unit TEXT
      ) AS $$
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
      $$ LANGUAGE plpgsql;
    `;

    try {
      console.log('Создаем таблицу и функции в базе данных...');
      await this.pool.query(createTableSQL);
      console.log('✅ Таблица и функции успешно созданы');
      return true;
    } catch (error) {
      console.error('❌ Ошибка создания таблицы:', error.message);
      return false;
    }
  }

  async uploadRecipes(recipes) {
    console.log(`Загружаем ${recipes.length} исторических рецептов...`);
    
    let successCount = 0;
    let errorCount = 0;
    
    const insertSQL = `
      INSERT INTO drinks_recipes (
        recipe_number, name, ingredients, ingredients_per_liter, 
        description, original_text, source_type, historical_measures
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
      RETURNING id, name
    `;
    
    for (const recipe of recipes) {
      try {
        const values = [
          recipe.recipe_number,
          recipe.name,
          recipe.ingredients,
          recipe.ingredients_per_liter,
          recipe.description,
          recipe.original_text.substring(0, 5000), // ограничиваем длину
          recipe.source_type,
          recipe.historical_measures
        ];
        
        const result = await this.pool.query(insertSQL, values);
        console.log(`✅ Загружен: "${recipe.name}" (ID: ${result.rows[0].id})`);
        successCount++;
        
      } catch (error) {
        console.error(`❌ Ошибка загрузки "${recipe.name}":`, error.message);
        errorCount++;
      }
    }
    
    return { successCount, errorCount };
  }
}

// Функция для получения текста из Google Docs
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

// Основная функция
async function main() {
  const pool = new Pool(dbConfig);
  
  try {
    console.log('🍺 ИЗВЛЕЧЕНИЕ ИСТОРИЧЕСКИХ РЕЦЕПТОВ НАПИТКОВ');
    console.log('='.repeat(60));
    
    // Проверяем подключение к базе данных
    console.log('🔌 Проверяем подключение к базе данных...');
    const testResult = await pool.query('SELECT NOW()');
    console.log('✅ Подключение к PostgreSQL успешно');
    
    const parser = new HistoricalRecipeParser(pool);
    
    // Получаем аргументы командной строки
    const args = process.argv.slice(2);
    const testMode = args.includes('--test') || args.includes('-t');
    const useLocalFile = args.find(arg => arg.startsWith('--file='));
    
    // Получаем текст документа
    let documentText;
    if (useLocalFile) {
      const filepath = useLocalFile.split('=')[1];
      documentText = await fs.readFile(filepath, 'utf8');
    } else {
      const documentId = '1XO0_BqH-e4B-_F4hrzKKuaX6w8CR2IARgvAsfjQnAKA';
      documentText = await fetchGoogleDocsText(documentId);
    }
    
    if (!documentText || documentText.length < 100) {
      throw new Error('Документ слишком короткий или пустой');
    }
    
    // Парсим рецепты
    console.log('📜 Парсим исторические рецепты...');
    const recipes = parser.parseHistoricalRecipes(documentText);
    
    console.log(`📋 Найдено ${recipes.length} рецептов`);
    
    if (recipes.length === 0) {
      throw new Error('Не найдено рецептов для обработки');
    }
    
    // Режим тестирования
    if (testMode) {
      console.log('\n🧪 РЕЖИМ ТЕСТИРОВАНИЯ - без загрузки в БД');
      recipes.slice(0, 3).forEach((recipe, index) => {
        console.log(`\n📜 Рецепт ${index + 1}:`);
        console.log(`   Номер: ${recipe.recipe_number}`);
        console.log(`   Название: ${recipe.name}`);
        const ingredients = JSON.parse(recipe.ingredients);
        console.log(`   Ингредиентов: ${ingredients.length}`);
        ingredients.forEach(ing => {
          console.log(`     • ${ing.name}: ${ing.amount} ${ing.unit} (${ing.modern_amount} ${ing.modern_unit})`);
        });
      });
      console.log('\n✅ Тестирование завершено');
      return;
    }
    
    // Создаем таблицу
    const tableCreated = await parser.createTable();
    if (!tableCreated) {
      throw new Error('Не удалось создать таблицу');
    }
    
    // Показываем пример
    console.log('\n📝 Пример обработанного рецепта:');
    const exampleIngredients = JSON.parse(recipes[0].ingredients);
    console.log(`Рецепт: ${recipes[0].name}`);
    console.log(`Ингредиенты (${exampleIngredients.length}):`);
    exampleIngredients.slice(0, 3).forEach(ing => {
      console.log(`  • ${ing.name}: ${ing.amount} ${ing.unit} → ${ing.modern_amount} ${ing.modern_unit}`);
    });
    
    // Загружаем в базу данных
    console.log('\n💾 Загружаем в базу данных...');
    const result = await parser.uploadRecipes(recipes);
    
    console.log(`\n🎉 Завершено! Загружено: ${result.successCount}, ошибок: ${result.errorCount}`);
    
    if (result.successCount > 0) {
      console.log('\n🔍 Примеры SQL-запросов:');
      console.log(`
-- Все рецепты:
SELECT recipe_number, name FROM drinks_recipes ORDER BY recipe_number;

-- Поиск рецептов с водкой:
SELECT name, recipe_number FROM drinks_recipes 
WHERE ingredients::text ILIKE '%водка%';

-- Статистика ингредиентов:
SELECT * FROM get_ingredient_statistics() LIMIT 10;

-- Поиск по ингредиенту:
SELECT * FROM search_recipes_by_ingredient('анис');
      `);
    }
    
  } catch (error) {
    console.error('\n💥 КРИТИЧЕСКАЯ ОШИБКА:', error.message);
    process.exit(1);
  } finally {
    await pool.end();
  }
}

// Запуск
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(error => {
    console.error('Необработанная ошибка:', error);
    process.exit(1);
  });
}

export { main, HistoricalRecipeParser };
