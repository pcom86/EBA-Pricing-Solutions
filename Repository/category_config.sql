USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[category_config]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[category_config](
	[scheme_id] [uniqueidentifier] NOT NULL,
	[product] [nvarchar](100) NOT NULL,
	[variant] [nvarchar](100) NOT NULL,
	[category_number] [int] NOT NULL,
	[category_name] [nvarchar](200) NULL,
	[category_flag] [float] NULL,
	[cover_type] [nvarchar](100) NULL,
	[benefit_multiple] [float] NULL,
	[fields] [nvarchar](max) NOT NULL,
 CONSTRAINT [PK_category_config_v2] PRIMARY KEY CLUSTERED 
(
	[scheme_id] ASC,
	[product] ASC,
	[variant] ASC,
	[category_number] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO
ALTER TABLE [dbo].[category_config] ADD  DEFAULT ('{}') FOR [fields]
GO
ALTER TABLE [dbo].[category_config]  WITH CHECK ADD  CONSTRAINT [FK_category_config_v2_scheme] FOREIGN KEY([scheme_id])
REFERENCES [dbo].[scheme] ([scheme_id])
GO
ALTER TABLE [dbo].[category_config] CHECK CONSTRAINT [FK_category_config_v2_scheme]
GO
ALTER TABLE [dbo].[category_config]  WITH CHECK ADD  CONSTRAINT [CK_category_config_v2_json] CHECK  ((isjson([fields])=(1)))
GO
ALTER TABLE [dbo].[category_config] CHECK CONSTRAINT [CK_category_config_v2_json]
GO
