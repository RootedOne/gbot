import os
import matplotlib
matplotlib.use('Agg')  # Headless backend
import matplotlib.pyplot as plt

def get_irr_equivalent(amount: float, currency: str) -> float:
    c = currency.upper()
    if c in ("IRR", "TOMAN"):
        return amount
    elif c == "USD":
        return amount * 60000.0  # visual estimation rate
    elif c in ("STARS", "STAR"):
        return amount * 1000.0   # visual estimation rate
    return amount

def generate_income_chart(stats: dict, node_id: int = 0, output_path: str = "/tmp/income_chart.png") -> str:
    """Generates a premium dark-slate style chart visualization based on income statistics.
    
    For node_id = 0: Pie chart of revenue sources (Main Retail, Reseller Wholesale, Reseller Retail).
    For node_id > 0: Bar chart of Top Plans by sales.
    """
    # 1. Base style setup
    plt.rcParams['figure.facecolor'] = '#1E1E2E'
    plt.rcParams['axes.facecolor'] = '#1E1E2E'
    plt.rcParams['text.color'] = '#CDD6F4'
    plt.rcParams['axes.labelcolor'] = '#CDD6F4'
    plt.rcParams['xtick.color'] = '#CDD6F4'
    plt.rcParams['ytick.color'] = '#CDD6F4'
    plt.rcParams['font.sans-serif'] = 'sans-serif'
    plt.rcParams['font.family'] = 'sans-serif'
    
    # Ensure target directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Check if empty data
    total_sales = sum(p.get("sales_count", 0) for p in stats.get("popular_plans", []))
    
    fig, ax = plt.subplots(figsize=(6, 5))
    
    if total_sales == 0:
        # Render empty placeholder chart
        ax.text(0.5, 0.5, "No sales recorded yet\n🧾 Waiting for first orders!", 
                ha='center', va='center', fontsize=14, color='#A6ADC8')
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        plt.title("Revenue Summary", fontsize=16, color='#CDD6F4', pad=20, weight='bold')
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, facecolor='#1E1E2E')
        plt.close()
        return output_path

    if node_id == 0:
        # Pie chart for global breakdown of revenue sources (Main Bot vs Resellers)
        sources = stats.get("sources", {})
        
        # Calculate IRR equivalents for visual proportions
        main_retail_val = sum(get_irr_equivalent(v, cur) for cur, v in sources.get("main_retail", {}).items())
        reseller_topup_val = sum(get_irr_equivalent(v, cur) for cur, v in sources.get("reseller_topup", {}).items())
        reseller_retail_val = sum(get_irr_equivalent(v, cur) for cur, v in sources.get("reseller_retail", {}).items())
        
        labels = []
        sizes = []
        colors = []
        
        if main_retail_val > 0:
            labels.append("Main Bot Retail")
            sizes.append(main_retail_val)
            colors.append("#7B2CBF") # Purple
        if reseller_topup_val > 0:
            labels.append("Reseller Wholesale")
            sizes.append(reseller_topup_val)
            colors.append("#3A86C8") # Blue
        if reseller_retail_val > 0:
            labels.append("Resellers Retail")
            sizes.append(reseller_retail_val)
            colors.append("#06D6A0") # Teal

        if not sizes:
            # Fallback to general order distribution
            labels = ["No Source Data"]
            sizes = [1]
            colors = ["#585B70"]

        wedges, texts, autotexts = ax.pie(
            sizes, 
            labels=labels, 
            autopct='%1.1f%%',
            startangle=140,
            colors=colors,
            textprops=dict(color='#CDD6F4', weight='bold'),
            wedgeprops=dict(width=0.4, edgecolor='#1E1E2E', linewidth=3) # Donut chart style
        )
        
        # Style inner autotexts
        for autotext in autotexts:
            autotext.set_color('#11111B')
            autotext.set_size(10)
            
        plt.title("Revenue Source Breakdown", fontsize=16, color='#CDD6F4', pad=20, weight='bold')
    else:
        # Horizontal bar chart for Reseller Admin showing popular custom plans
        popular = stats.get("popular_plans", [])
        
        # Group by plan title and sum counts
        plan_counts = {}
        for item in popular:
            title = item["title"]
            plan_counts[title] = plan_counts.get(title, 0) + item["sales_count"]
            
        # Sort plans
        sorted_plans = sorted(plan_counts.items(), key=lambda x: x[1])
        titles = [x[0] for x in sorted_plans]
        counts = [x[1] for x in sorted_plans]
        
        bars = ax.barh(titles, counts, color='#89B4FA', edgecolor='#1E1E2E', height=0.6)
        
        # Customize ticks and grid
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['bottom'].set_color('#585B70')
        ax.spines['left'].set_color('#585B70')
        
        ax.xaxis.grid(True, linestyle='--', alpha=0.3, color='#585B70')
        ax.set_axisbelow(True)
        
        # Add values on the bars
        for bar in bars:
            width = bar.get_width()
            ax.text(
                width + 0.1, 
                bar.get_y() + bar.get_height()/2, 
                f"{int(width)}", 
                ha='left', 
                va='center', 
                color='#CDD6F4', 
                weight='bold'
            )
            
        plt.title("Top Plans by Sales Count", fontsize=16, color='#CDD6F4', pad=20, weight='bold')
        plt.xlabel("Number of Sales", labelpad=10, weight='bold')
        
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, facecolor='#1E1E2E')
    plt.close()
    return output_path
